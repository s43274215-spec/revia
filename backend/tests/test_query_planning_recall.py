import json
import unittest
import uuid

from app.ai.clients.base import AIClient
from app.ai.service import AIService
from app.matching.query_planning import QueryPlanner, SyllabusItemType
from app.matching.service import MatchingService
from app.models.document import TextChunk
from app.services.generation import GenerationWorkflowService
from app.syllabus.parser import ParsedSyllabusItem


DOCUMENT_ID = uuid.uuid4()


def chunk(position: int, text: str, *, page: int | None = None, section: str | None = None) -> TextChunk:
    return TextChunk(
        id=uuid.uuid4(),
        parsed_document_id=DOCUMENT_ID,
        position=position,
        page_start=page or position + 1,
        page_end=page or position + 1,
        chapter_title="人力资源管理",
        section_title=section,
        content=text,
    )


class QueryPlanningRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matcher = MatchingService(threshold=0.35, fallback_threshold=0.28, max_candidates=6)

    def test_herzberg_alias_recalls_motivation_hygiene_theory(self) -> None:
        source = chunk(0, "激励—保健理论把影响工作满意度的因素分为激励因素和保健因素。", page=101)
        match = self.matcher.match_item(syllabus_item="赫茨伯格双因素理论", syllabus_chapter=None, chunks=[source])
        self.assertTrue(match.matched)
        self.assertEqual(match.candidates[0].chunk_id, source.id)
        self.assertIn("激励-保健理论", match.candidates[0].matched_queries)

    def test_xy_alias_recalls_mcgregor_wording(self) -> None:
        source = chunk(0, "麦格雷戈X理论与Y理论体现两类不同的人性假设。", page=90)
        match = self.matcher.match_item(syllabus_item="X-Y 理论", syllabus_chapter=None, chunks=[source])
        self.assertTrue(match.matched)
        self.assertEqual(match.candidates[0].chunk_id, source.id)

    def test_work_description_aliases_cover_position_and_post_wording(self) -> None:
        sources = [
            chunk(0, "职位说明书由职位描述和任职资格组成。", page=326),
            chunk(1, "岗位说明书用于明确工作职责与任职条件。", page=327),
        ]
        match = self.matcher.match_item(syllabus_item="工作说明书", syllabus_chapter=None, chunks=sources)
        self.assertEqual({candidate.chunk_id for candidate in match.candidates}, {item.id for item in sources})

    def test_composite_planning_splits_and_preserves_subquery_coverage(self) -> None:
        plan = QueryPlanner().plan_item("内部供给、外部供给及总供给计算")
        self.assertEqual(plan.item_type, SyllabusItemType.COMPOSITE)
        self.assertEqual(len(plan.subqueries), 3)
        sources = [
            chunk(0, "内部人力资源供给预测可使用技能清单和人员接替图。", page=244),
            chunk(1, "外部人力资源供给预测需要分析劳动力市场。", page=250),
            chunk(2, "人力资源供给总量结合供需平衡进行计算。", page=257),
        ]
        match = self.matcher.match_plan(plan=plan, chunks=sources)
        covered = {
            query
            for candidate in match.candidates
            for query in candidate.matched_queries
            if query in plan.subqueries
        }
        self.assertEqual(covered, set(plan.subqueries))

    def test_collection_parent_uses_child_evidence(self) -> None:
        entries = [
            ParsedSyllabusItem(None, "主要模型"),
            ParsedSyllabusItem(None, "冰山模型", "主要模型"),
            ParsedSyllabusItem(None, "洋葱模型", "主要模型"),
        ]
        sources = [
            chunk(0, "冰山模型区分水面以上和水面以下的胜任素质。", page=425),
            chunk(1, "洋葱模型按层次呈现知识、技能、动机和个性。", page=426),
        ]
        plans = self.matcher.plan_items(entries)
        matches = [self.matcher.match_plan(plan=plan, chunks=sources) for plan in plans]
        resolved = self.matcher.resolve_dependent_matches(plans, matches)
        self.assertTrue(resolved[0].matched)
        self.assertEqual({candidate.chunk_id for candidate in resolved[0].candidates}, {item.id for item in sources})

    def test_task_reuses_nearby_model_evidence_without_literal_search(self) -> None:
        entries = [
            ParsedSyllabusItem("胜任素质", "冰山模型"),
            ParsedSyllabusItem("胜任素质", "结合模型做案例／论述分析"),
        ]
        source = chunk(0, "冰山模型包括知识、技能、自我形象、个性和动机。", page=425)
        plans = self.matcher.plan_items(entries)
        self.assertEqual(plans[1].item_type, SyllabusItemType.TASK)
        self.assertEqual(plans[1].retrieval_queries, ())
        matches = [self.matcher.match_plan(plan=plan, chunks=[source]) for plan in plans]
        resolved = self.matcher.resolve_dependent_matches(plans, matches)
        self.assertTrue(resolved[1].matched)
        self.assertEqual(resolved[1].candidates[0].chunk_id, source.id)
        self.assertEqual(resolved[1].matching_query, "reused_hierarchy_evidence")

    def test_multi_query_fusion_deduplicates_the_same_chunk(self) -> None:
        source = chunk(0, "赫茨伯格双因素理论也称激励-保健理论。", page=100)
        match = self.matcher.match_item(syllabus_item="双因素理论", syllabus_chapter=None, chunks=[source])
        self.assertEqual(len(match.candidates), 1)
        self.assertGreater(len(match.candidates[0].matched_queries), 1)

    def test_unrelated_alias_does_not_create_high_score_recall(self) -> None:
        unrelated = chunk(0, "财务报表分析资产、负债、利润和现金流量。", page=500)
        match = self.matcher.match_item(syllabus_item="工作说明书", syllabus_chapter=None, chunks=[unrelated])
        self.assertFalse(match.matched)

    def test_missing_material_remains_unmatched(self) -> None:
        source = chunk(0, "招聘流程包括需求确认、发布信息、甄选和录用。", page=445)
        match = self.matcher.match_item(syllabus_item="量子纠缠实验", syllabus_chapter=None, chunks=[source])
        self.assertFalse(match.matched)

    def test_calculation_phrase_is_composite_not_collection(self) -> None:
        plan = QueryPlanner().plan_item("需掌握内部供给、外部供给的计算逻辑，总供给的计算方法是核心考点。")
        self.assertEqual(plan.item_type, SyllabusItemType.COMPOSITE)


class CountingRewriteClient(AIClient):
    def __init__(self) -> None:
        self.calls = 0

    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return json.dumps({"queries": ["教材中的抽象概念"]}, ensure_ascii=False)


class QueryRewriteFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_fallback_runs_once_per_query_after_deterministic_failure(self) -> None:
        matcher = MatchingService(threshold=0.35, fallback_threshold=0.28, max_candidates=6)
        plans = [QueryPlanner().plan_item("抽象概念"), QueryPlanner().plan_item("抽象概念")]
        matches = [matcher.match_plan(plan=plan, chunks=[]) for plan in plans]
        client = CountingRewriteClient()
        workflow = GenerationWorkflowService(
            db=None,  # This helper performs no database access.
            workspace_id=uuid.uuid4(),
            ai_service=AIService(client),
            matching_service=matcher,
            provider_name="test",
        )
        rewritten_plans, rewritten_matches = await workflow._apply_query_rewrite_fallbacks(plans, matches, [])
        self.assertEqual(client.calls, 1)
        self.assertTrue(all(plan.used_ai_fallback for plan in rewritten_plans))
        self.assertTrue(all(match.used_ai_fallback for match in rewritten_matches))


if __name__ == "__main__":
    unittest.main()
