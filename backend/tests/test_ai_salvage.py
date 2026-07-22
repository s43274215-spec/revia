import json
import unittest
import uuid

from app.ai.validation import AIOutputValidationError, salvage_generated_item, validate_generated_item
from app.matching.schemas import CandidateChunk


class AIOutputSalvageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunk_id = uuid.uuid4()
        self.candidates = [CandidateChunk(
            syllabus_item="绩效管理",
            chunk_id=self.chunk_id,
            score=0.95,
            chapter="绩效管理",
            section="基本流程",
            page_start=12,
            page_end=13,
            text="绩效管理包括目标设定、过程沟通和结果评价。",
        )]

    def test_duplicate_original_recitation_and_parent_title_are_saved(self) -> None:
        content = "绩效管理包括目标设定、过程沟通和结果评价。"
        raw = json.dumps({
            "knowledge_point_title": "绩效管理",
            "bullet_points": [{
                "title": "绩效管理",
                "original": {"title": "绩效管理", "content": content},
                "recitation": {"title": "绩效管理", "content": content},
                "keywords": {"title": "绩效管理", "content": "目标设定、过程沟通、结果评价"},
                "source_chunk_ids": [str(self.chunk_id)],
                "source_pages": [12],
            }],
        }, ensure_ascii=False)

        result = validate_generated_item(raw, fallback_title="绩效管理")

        self.assertEqual(result.bullet_points[0].original.content, result.bullet_points[0].recitation.content)
        self.assertIn("背诵版与原文版内容相同", result.format_warnings)
        self.assertIn("要点标题与知识点标题相同，展示时已隐藏", result.format_warnings)

    def test_salvage_fills_missing_version_and_filters_invalid_sources(self) -> None:
        raw = json.dumps({
            "knowledge_point_title": "招聘与选拔",
            "bullet_points": [{
                "title": "招聘与选拔",
                "original": {"content": "招聘与选拔需要依据岗位要求筛选候选人。"},
                "keywords": ["岗位要求", "候选人筛选"],
                "source_chunk_ids": [str(uuid.uuid4())],
                "source_pages": [999],
                "unexpected": "ignored",
            }],
        }, ensure_ascii=False)

        result = salvage_generated_item(
            [raw],
            fallback_title="招聘与选拔",
            candidates=self.candidates,
        )

        bullet = result.bullet_points[0]
        self.assertEqual(bullet.recitation.content, bullet.original.content)
        self.assertEqual(bullet.source_chunk_ids, [])
        self.assertEqual(bullet.source_pages, [])
        self.assertIn("来源未完整验证", result.format_warnings)
        self.assertIn("部分无效来源已移除", result.format_warnings)

    def test_salvage_skips_unreadable_bullet_but_keeps_readable_bullet(self) -> None:
        raw = json.dumps({
            "knowledge_point_title": "绩效管理",
            "bullet_points": [
                {"title": "损坏", "original": "-", "recitation": "", "keywords": ""},
                {
                    "title": "基本流程",
                    "original": "绩效管理包括目标设定与结果反馈。",
                    "recitation": "绩效管理包括目标设定与结果反馈。",
                    "keywords": "目标、反馈",
                    "source_chunk_ids": [str(self.chunk_id)],
                    "source_pages": [12],
                },
            ],
        }, ensure_ascii=False)

        result = salvage_generated_item([raw], fallback_title="绩效管理", candidates=self.candidates)

        self.assertEqual(len(result.bullet_points), 1)
        self.assertEqual(result.bullet_points[0].title, "基本流程")

    def test_salvage_recovers_explicit_content_from_invalid_json(self) -> None:
        raw = """
```json
{
  "knowledge_point_title": "胜任素质模型",
  "bullet_points": [{
    "title": "胜任素质模型",
    "original": {
      "title": "胜任素质模型",
      "content": "胜任素质模型用于描述产生优秀绩效所需要的知识、技能和行为特征。"
    },
    "recitation": {
      "title": "胜任素质模型",
      "content": "胜任素质模型概括优秀绩效所需的知识、技能与行为特征。"
    },
    "keywords": {
      "title": "胜任素质模型",
      "content": "知识、技能、行为特征"
    }
```
"""

        result = salvage_generated_item(
            [raw],
            fallback_title="胜任素质模型",
            candidates=self.candidates,
        )

        bullet = result.bullet_points[0]
        self.assertIn("优秀绩效", bullet.original.content)
        self.assertIn("知识", bullet.keywords.content)
        self.assertEqual(bullet.source_chunk_ids, [])
        self.assertEqual(bullet.source_pages, [])
        self.assertIn("AI 返回结构损坏，已从可读文本中恢复内容", result.format_warnings)
        self.assertIn("来源未完整验证", result.format_warnings)

    def test_invalid_json_without_explicit_readable_content_still_fails(self) -> None:
        raw = '{"knowledge_point_title":"绩效管理","bullet_points":[{"title":"损坏"'

        with self.assertRaises(AIOutputValidationError):
            salvage_generated_item([raw], fallback_title="绩效管理", candidates=self.candidates)

    def test_salvage_still_fails_when_nothing_is_readable(self) -> None:
        raw = json.dumps({
            "knowledge_point_title": "绩效管理",
            "bullet_points": [{"title": "损坏", "original": "-", "recitation": "", "keywords": ""}],
        }, ensure_ascii=False)

        with self.assertRaises(AIOutputValidationError):
            salvage_generated_item([raw], fallback_title="绩效管理", candidates=self.candidates)


if __name__ == "__main__":
    unittest.main()
