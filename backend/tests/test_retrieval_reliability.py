import unittest
import uuid

from app.matching.service import MatchingService
from app.models.document import TextChunk


def chunk(
    position: int,
    text: str,
    *,
    page: int | None = None,
    chapter: str = "第一章 招聘管理",
    section: str | None = None,
) -> TextChunk:
    page_number = page if page is not None else position + 1
    return TextChunk(
        id=uuid.uuid4(),
        parsed_document_id=DOCUMENT_ID,
        position=position,
        page_start=page_number,
        page_end=page_number,
        chapter_title=chapter,
        section_title=section,
        content=text,
    )


DOCUMENT_ID = uuid.uuid4()


class RetrievalReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matcher = MatchingService(threshold=0.35, max_candidates=6)

    def test_short_structural_title_does_not_displace_body_evidence(self) -> None:
        title = chunk(0, "1.2 招聘流程", section="1.2 招聘流程")
        bodies = [
            chunk(index, f"招聘流程的正文说明包含需求确认、渠道选择和甄选步骤。补充内容{index}。" * 2)
            for index in range(1, 5)
        ]
        match = self.matcher.match_item(
            syllabus_item="招聘流程步骤与方法",
            syllabus_chapter="第一章 招聘管理",
            chunks=[title, *bodies],
        )
        self.assertTrue(match.matched)
        self.assertNotEqual(match.candidates[0].chunk_id, title.id)

    def test_initial_recall_keeps_six_candidates_beyond_the_old_top_three(self) -> None:
        chunks = [
            chunk(index, f"招聘流程包括需求分析、发布信息、人员甄选和录用。案例说明{index}。" * 2)
            for index in range(6)
        ]
        match = self.matcher.match_item(
            syllabus_item="招聘流程",
            syllabus_chapter="第一章 招聘管理",
            chunks=chunks,
        )
        self.assertEqual(len(match.candidates), 6)
        self.assertIn(chunks[5].id, {candidate.chunk_id for candidate in match.candidates})

    def test_generation_evidence_is_deduplicated_and_limited_to_four(self) -> None:
        duplicate = "招聘流程定义包括需求确认、渠道选择、甄选和录用。" * 3
        chunks = [chunk(0, duplicate), chunk(1, duplicate)] + [
            chunk(index, f"招聘流程的补充案例与计算方法说明{index}。" * 4)
            for index in range(2, 7)
        ]
        match = self.matcher.match_item(
            syllabus_item="招聘流程",
            syllabus_chapter="第一章 招聘管理",
            chunks=chunks,
        )
        evidence = self.matcher.select_generation_evidence(match=match, chunks=chunks)
        self.assertLessEqual(len(evidence), 4)
        self.assertEqual(len({candidate.text for candidate in evidence}), len(evidence))

    def test_related_adjacent_chunk_adds_steps_and_examples(self) -> None:
        main = chunk(0, "招聘流程用于系统组织招聘活动。" * 4)
        adjacent = chunk(1, "招聘流程步骤包括需求确认和甄选，例如校园招聘案例。" * 3)
        match = self.matcher.match_item(
            syllabus_item="招聘流程",
            syllabus_chapter="第一章 招聘管理",
            chunks=[main],
        )
        evidence = self.matcher.select_generation_evidence(match=match, chunks=[main, adjacent])
        self.assertIn(adjacent.id, {candidate.chunk_id for candidate in evidence})

    def test_unrelated_adjacent_chunk_is_not_added(self) -> None:
        main = chunk(0, "招聘流程用于系统组织招聘活动。" * 4)
        adjacent = chunk(1, "企业财务报表反映资产负债和现金流量。" * 3)
        match = self.matcher.match_item(
            syllabus_item="招聘流程",
            syllabus_chapter="第一章 招聘管理",
            chunks=[main],
        )
        evidence = self.matcher.select_generation_evidence(match=match, chunks=[main, adjacent])
        self.assertNotIn(adjacent.id, {candidate.chunk_id for candidate in evidence})

    def test_primary_and_fallback_thresholds_remain_effective(self) -> None:
        relevant = chunk(0, "招聘管理包括招聘规划、招聘流程、甄选方法和录用管理。" * 3)
        fallback_matcher = MatchingService(threshold=0.9, fallback_threshold=0.28, max_candidates=6)
        fallback = fallback_matcher.match_item(
            syllabus_item="招聘流程",
            syllabus_chapter="第一章 招聘管理",
            chunks=[relevant],
        )
        self.assertTrue(fallback.matched)
        self.assertEqual(fallback.recall_stage, "secondary")
        unrelated = chunk(1, "财务报表和现金流量分析。" * 4)
        unmatched = self.matcher.match_item(
            syllabus_item="招聘流程与甄选方法",
            syllabus_chapter=None,
            chunks=[unrelated],
        )
        self.assertFalse(unmatched.matched)


if __name__ == "__main__":
    unittest.main()
