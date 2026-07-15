import json
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every ORM model
from app.ai.clients.base import AIClient, AIConfigurationError
from app.ai.clients.factory import build_ai_client
from app.ai.prompt_builder import PromptBuilder
from app.ai.service import AIService, ItemGenerationRequest
from app.ai.validation import AIOutputValidationError, validate_generated_item
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.matching.schemas import CandidateChunk
from app.matching.service import MatchingService
from app.models.content import BulletPoint, BulletPointSource, Chapter, ContentVersion
from app.models.document import TextChunk
from app.models.project import GenerationJob, Project
from app.models.workspace import Workspace
from app.syllabus.parser import SyllabusParser
from tests.helpers import authorization_header
from tests.test_document_processing import build_test_pdf


class SequenceClient(AIClient):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return self.outputs.pop(0)


class SyllabusAndMatchingTests(unittest.TestCase):
    def test_syllabus_parser_supports_chapters_numbering_plain_lines_and_deduplication(self) -> None:
        parsed = SyllabusParser().parse(
            "\n第三章 市场失灵\n1. 外部性\n2、公共物品\n公共物品\n***\n信息不对称\n\n"
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].chapter, "第三章 市场失灵")
        self.assertEqual(parsed[0].items, ["外部性", "公共物品", "信息不对称"])
        unchaptered = SyllabusParser().parse("外部性\n公共物品")
        self.assertIsNone(unchaptered[0].chapter)

    def test_matching_limits_candidates_and_marks_unmatched(self) -> None:
        chunks = [
            TextChunk(
                id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=0,
                page_start=12, page_end=13, chapter_title="第三章 市场失灵",
                section_title="3.1 外部性", content="外部性影响第三方且没有计入市场价格。",
            ),
            TextChunk(
                id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=1,
                page_start=14, page_end=15, chapter_title="第三章 市场失灵",
                section_title=None, content="负外部性会造成社会成本与私人成本偏离。",
            ),
            TextChunk(
                id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=2,
                page_start=30, page_end=30, chapter_title="第四章 宏观经济",
                section_title=None, content="财政政策影响总需求。",
            ),
        ]
        matcher = MatchingService(threshold=0.35, max_candidates=1)
        matched = matcher.match_item(
            syllabus_item="外部性", syllabus_chapter="第三章 市场失灵", chunks=chunks
        )
        self.assertTrue(matched.matched)
        self.assertEqual(len(matched.candidates), 1)
        unmatched = matcher.match_item(
            syllabus_item="量子纠缠", syllabus_chapter=None, chunks=chunks
        )
        self.assertFalse(unmatched.matched)
        self.assertEqual(unmatched.candidates, [])
        self.assertIn("unmatched", unmatched.reason or "")
        print("CANDIDATE_CHUNK_RESULT=" + matched.candidates[0].model_dump_json())

    def test_matching_preprocesses_exam_notes_and_uses_controlled_expansions(self) -> None:
        matcher = MatchingService(threshold=0.35, max_candidates=3)
        relationship_chunk = TextChunk(
            id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=0,
            page_start=16, page_end=28, chapter_title=None, section_title=None,
            content="人口资源、人力资源与人才资源是什么关系？三者的内涵和外延不同。",
        )
        match = matcher.match_item(
            syllabus_item="人口、人力、人才的关系：需掌握三者的内涵与外延区别。",
            syllabus_chapter=None,
            chunks=[relationship_chunk],
        )
        self.assertTrue(match.matched)
        self.assertEqual(match.syllabus_item_original, "人口、人力、人才的关系：需掌握三者的内涵与外延区别。")
        self.assertEqual(match.matching_query, "人口、人力、人才的关系")
        self.assertEqual(match.candidates[0].syllabus_item, match.syllabus_item_original)

    def test_secondary_recall_requires_direct_or_multi_keyword_evidence(self) -> None:
        matcher = MatchingService(threshold=0.9, max_candidates=3)
        relevant = TextChunk(
            id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=0,
            page_start=47, page_end=57, chapter_title=None, section_title=None,
            content="人力资源管理包括人力资源规划、招聘、培训、绩效管理、薪酬管理和员工关系。",
        )
        match = matcher.match_item(
            syllabus_item="人力资源管理的核心内容：规划、招聘、培训、绩效管理、薪酬管理、员工关系六大模块；需区分管理内容和管理目的。",
            syllabus_chapter=None,
            chunks=[relevant],
        )
        self.assertTrue(match.matched)
        self.assertEqual(match.recall_stage, "secondary")

        false_positive = TextChunk(
            id=uuid.uuid4(), parsed_document_id=uuid.uuid4(), position=1,
            page_start=58, page_end=63, chapter_title=None, section_title=None,
            content="企业需要合理使用人力资源，同时控制资本投入并提升招聘效率。",
        )
        unmatched = MatchingService(threshold=0.35, max_candidates=3).match_item(
            syllabus_item="人力资本：基本内涵是核心考点，以理解为主，无需死记硬背。",
            syllabus_chapter=None,
            chunks=[false_positive],
        )
        self.assertFalse(unmatched.matched)


class AIValidationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_json_triggers_exactly_one_structure_repair(self) -> None:
        chunk_id = uuid.uuid4()
        valid = json.dumps({
            "knowledge_point_title": "外部性",
            "bullet_points": [{
                "title": "定义与影响",
                "original": {"title": "定义与影响", "content": "外部性影响第三方且没有计入市场价格。"},
                "recitation": {"title": "定义与影响", "content": "定义：经济活动影响第三方，但该影响没有通过价格体现。"},
                "keywords": {"title": "定义与影响", "content": "第三方、市场价格、外部影响"},
                "source_chunk_ids": [str(chunk_id)],
                "source_pages": [12],
            }],
        }, ensure_ascii=False)
        client = SequenceClient(["not-json", valid])
        result = await AIService(client).generate_item(ItemGenerationRequest(
            project_id=uuid.uuid4(),
            project_name="经济学",
            project_description=None,
            syllabus_chapter="第三章 市场失灵",
            syllabus_item="外部性",
            candidates=[CandidateChunk(
                syllabus_item="外部性", chunk_id=chunk_id, score=0.9,
                chapter="第三章 市场失灵", section="3.1 外部性",
                page_start=12, page_end=13, text="外部性影响第三方。",
            )],
        ))
        self.assertEqual(result.knowledge_point_title, "外部性")
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("REVIA_REPAIR_ITEM_JSON_V1", client.prompts[1])

    async def test_second_invalid_response_fails_without_an_unbounded_retry(self) -> None:
        chunk_id = uuid.uuid4()
        client = SequenceClient(["not-json", "still-not-json"])
        with self.assertRaisesRegex(AIOutputValidationError, "after one structure-repair retry"):
            await AIService(client).generate_item(ItemGenerationRequest(
                project_id=uuid.uuid4(),
                project_name="经济学",
                project_description=None,
                syllabus_chapter=None,
                syllabus_item="外部性",
                candidates=[CandidateChunk(
                    syllabus_item="外部性", chunk_id=chunk_id, score=0.9,
                    chapter=None, section=None, page_start=1, page_end=1,
                    text="外部性影响第三方。",
                )],
            ))
        self.assertEqual(len(client.prompts), 2)

    def test_live_mode_without_key_is_an_explicit_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as storage:
            settings = Settings(
                _env_file=None,
                ai_mode="live",
                ai_provider="deepseek",
                deepseek_api_key=None,
                file_storage_root=storage,
            )
            with self.assertRaisesRegex(AIConfigurationError, "尚未配置 DeepSeek API Key"):
                build_ai_client(settings)


class PromptAndGeneratedItemSchemaTests(unittest.TestCase):
    @staticmethod
    def valid_payload(chunk_id: uuid.UUID) -> dict:
        return {
            "knowledge_point_title": "人力资源的数量和质量",
            "bullet_points": [{
                "title": "（一）人力资源数量",
                "original": {
                    "title": "（一）人力资源数量",
                    "content": "人力资源数量包括适龄就业人口、失业人口和其他能够参与社会劳动的人口。",
                },
                "recitation": {
                    "title": "（一）人力资源数量",
                    "content": "定义：人力资源数量是能够参与社会劳动的人口总量。要点包括适龄就业人口和失业人口。",
                },
                "keywords": {
                    "title": "（一）人力资源数量",
                    "content": "适龄就业人口、失业人口、人口总量",
                },
                "source_chunk_ids": [str(chunk_id)],
                "source_pages": [16],
            }],
        }

    def test_prompt_deduplicates_candidate_text_and_omits_irrelevant_project_metadata(self) -> None:
        first_id = uuid.uuid4()
        candidates = [
            CandidateChunk(
                syllabus_item="人力资源数量", chunk_id=first_id, score=0.8,
                chapter="第一章", section="数量", page_start=16, page_end=18,
                text="人力资源数量包括适龄就业人口。",
            ),
            CandidateChunk(
                syllabus_item="人力资源数量", chunk_id=uuid.uuid4(), score=0.7,
                chapter="第一章", section="数量", page_start=16, page_end=18,
                text="人力资源数量包括适龄就业人口。",
            ),
        ]
        project_id = uuid.uuid4()
        prompt = PromptBuilder().build_item(
            project_id=project_id,
            project_name="人力资源",
            project_description="不应重复发送的说明",
            syllabus_chapter="第一章",
            syllabus_item="人力资源数量",
            candidates=candidates,
        ).user_prompt
        serialized_context = prompt.split("SOURCE_CONTEXT_JSON_START", 1)[1].split(
            "SOURCE_CONTEXT_JSON_END", 1
        )[0].strip()
        context = json.loads(serialized_context)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["chunk_id"], str(first_id))
        self.assertNotIn(str(project_id), prompt)
        self.assertNotIn("不应重复发送的说明", prompt)

    def test_generated_item_enforces_title_hierarchy_keywords_and_ocr_cleanliness(self) -> None:
        chunk_id = uuid.uuid4()
        valid = self.valid_payload(chunk_id)
        result = validate_generated_item(json.dumps(valid, ensure_ascii=False))
        self.assertEqual(result.bullet_points[0].title, "（一）人力资源数量")

        long_topic = json.loads(json.dumps(valid, ensure_ascii=False))
        long_topic["knowledge_point_title"] = "内部维度：企业战略、企业文化、企业生命周期、领导风格"
        shortened = validate_generated_item(json.dumps(long_topic, ensure_ascii=False))
        self.assertEqual(shortened.knowledge_point_title, "内部维度")

        repeated_parent = json.loads(json.dumps(valid, ensure_ascii=False))
        repeated_title = "人力资源的数量和质量（一）数量"
        repeated_parent["bullet_points"][0]["title"] = repeated_title
        for kind in ("original", "recitation", "keywords"):
            repeated_parent["bullet_points"][0][kind]["title"] = repeated_title
        with self.assertRaisesRegex(AIOutputValidationError, "complete knowledge point title"):
            validate_generated_item(json.dumps(repeated_parent, ensure_ascii=False))

        too_few_keywords = json.loads(json.dumps(valid, ensure_ascii=False))
        too_few_keywords["bullet_points"][0]["keywords"]["content"] = "人口、数量"
        with self.assertRaisesRegex(AIOutputValidationError, "between 3 and 8"):
            validate_generated_item(json.dumps(too_few_keywords, ensure_ascii=False))

        ocr_noise = json.loads(json.dumps(valid, ensure_ascii=False))
        ocr_noise["bullet_points"][0]["original"]["content"] += " 严禁复制"
        with self.assertRaisesRegex(AIOutputValidationError, "OCR noise"):
            validate_generated_item(json.dumps(ocr_noise, ensure_ascii=False))


class GenerationPipelineAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.project_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=self.workspace_id))
            session.add(Project(
                id=self.project_id,
                workspace_id=self.workspace_id,
                name="西方经济学",
                description="生成链路测试",
            ))
            session.commit()

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
            ai_mode="mock",
            matching_threshold=0.35,
            matching_max_candidates=2,
            public_access_enabled=True,
        )
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.storage.cleanup()

    def test_full_mock_pipeline_persists_versions_sources_progress_and_is_idempotent(self) -> None:
        upload = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents",
            data={"kind": "course_material"},
            files={"file": ("economics.pdf", build_test_pdf(), "application/pdf")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        syllabus = self.client.put(
            f"/api/v1/projects/{self.project_id}/syllabus",
            json={"text": "第三章 市场失灵\n1. Externality\n2. Public goods\n3. Quantum mechanics"},
        )
        self.assertEqual(syllabus.status_code, 204, syllabus.text)

        generated = self.client.post(f"/api/v1/projects/{self.project_id}/generation-jobs")
        self.assertEqual(generated.status_code, 202, generated.text)
        accepted = generated.json()
        self.assertEqual(accepted["status"], "pending")

        status_response = self.client.get(
            f"/api/v1/projects/{self.project_id}/generation-jobs/{accepted['id']}"
        )
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.json()
        self.assertEqual(payload["status"], "partial_failed")
        self.assertEqual(payload["progress"], 100)
        self.assertEqual(payload["processed_items"], 3)
        self.assertEqual(payload["total_items"], 3)
        self.assertEqual(payload["item_failures"][0]["syllabus_item"], "Quantum mechanics")
        for state in ["pending", "parsing", "matching", "generating", "validating", "partial_failed"]:
            self.assertIn(state, payload["status_history"])

        self.assertEqual(status_response.json()["progress"], 100)

        material = self.client.get(f"/api/v1/projects/{self.project_id}/learning-material")
        self.assertEqual(material.status_code, 200, material.text)
        material_payload = material.json()
        self.assertEqual(len(material_payload["chapters"]), 1)
        bullets = [
            bullet
            for chapter in material_payload["chapters"]
            for point in chapter["knowledge_points"]
            for bullet in point["bullet_points"]
        ]
        self.assertEqual(len(bullets), 2)
        self.assertTrue(all(len(bullet["versions"]) == 3 for bullet in bullets))
        self.assertTrue(all(bullet["sources"] for bullet in bullets))

        with self.Session() as session:
            bullet_ids = list(session.scalars(select(BulletPoint.id)).all())
            version_rows = list(session.execute(
                select(ContentVersion.bullet_point_id, func.count(ContentVersion.id))
                .group_by(ContentVersion.bullet_point_id)
            ).all())
            self.assertEqual({row[0] for row in version_rows}, set(bullet_ids))
            self.assertTrue(all(row[1] == 3 for row in version_rows))
            self.assertEqual(session.scalar(select(func.count(BulletPointSource.id))), 2)
            initial_job_count = session.scalar(select(func.count(GenerationJob.id)))

        repeated = self.client.post(f"/api/v1/projects/{self.project_id}/generation-jobs")
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.json()["id"], payload["id"])
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count(GenerationJob.id))), initial_job_count)
            self.assertEqual(session.scalar(select(func.count(BulletPoint.id))), 2)

        regenerated = self.client.post(
            f"/api/v1/projects/{self.project_id}/generation-jobs?regenerate=true"
        )
        self.assertEqual(regenerated.status_code, 202, regenerated.text)
        self.assertNotEqual(regenerated.json()["id"], payload["id"])
        regenerated_status = self.client.get(
            f"/api/v1/projects/{self.project_id}/generation-jobs/{regenerated.json()['id']}"
        )
        self.assertEqual(regenerated_status.status_code, 200)
        self.assertEqual(regenerated_status.json()["status"], "partial_failed")
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count(Chapter.id))), 1)
            self.assertEqual(session.scalar(select(func.count(BulletPoint.id))), 2)
        print("GENERATION_JOB_RESULT=" + json.dumps(payload, ensure_ascii=False))

    def test_live_generation_endpoint_rejects_missing_key_without_mock_fallback(self) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
            ai_mode="live",
            ai_provider="deepseek",
            public_access_enabled=True,
        )
        response = self.client.post(f"/api/v1/projects/{self.project_id}/generation-jobs")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("尚未配置 DeepSeek API Key", response.json()["detail"])
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count(GenerationJob.id))), 0)


if __name__ == "__main__":
    unittest.main()
