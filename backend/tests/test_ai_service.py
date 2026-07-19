import json
import unittest
import uuid
from pathlib import Path

from app.ai.clients.mock import MockAIClient
from app.ai.service import AIService, StudyMaterialRequest
from app.ai.validation import AIOutputValidationError, validate_generated_project
from app.models.enums import ContentVersionKind


class AIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_generation_prompts_preserve_the_confirmed_version_semantics(self) -> None:
        prompt_root = Path(__file__).parents[1] / "app" / "ai" / "prompts"
        project_prompt = (prompt_root / "generate_three_versions.txt").read_text(encoding="utf-8")
        item_prompt = (prompt_root / "generate_item.txt").read_text(encoding="utf-8")

        self.assertIn("original 保留资料核心原始内容和完整表达", project_prompt)
        self.assertIn("recitation 在保持知识完整性的前提下", project_prompt)
        self.assertIn("keywords 压缩为关键概念和记忆线索", project_prompt)
        self.assertIn("保留资料核心原始内容和完整表达", item_prompt)
        self.assertIn("保持当前要点知识完整性的前提下", item_prompt)
        self.assertIn("压缩为 3—8 个能唤醒记忆的关键概念", item_prompt)

    async def test_mock_generation_matches_revia_structure(self) -> None:
        project_id = uuid.uuid4()
        result = await AIService(MockAIClient()).generate_study_material(
            StudyMaterialRequest(
                project_id=project_id,
                project_name="西方经济学",
                syllabus_text="外部性",
                source_context="当前阶段未解析 PDF 正文",
            )
        )

        self.assertEqual(result.project_id, project_id)
        bullet = result.chapters[0].knowledge_points[0].bullet_points[0]
        self.assertEqual({version.kind for version in bullet.versions}, set(ContentVersionKind))
        self.assertEqual(len({bullet.id for _ in bullet.versions}), 1)

    def test_invalid_output_is_rejected(self) -> None:
        invalid = json.dumps({"project_id": str(uuid.uuid4()), "chapters": []})
        with self.assertRaises(AIOutputValidationError):
            validate_generated_project(invalid)


if __name__ == "__main__":
    unittest.main()
