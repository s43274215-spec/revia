import unittest
import uuid
from types import SimpleNamespace

from app.models.enums import ContentVersionKind
from app.services.content_organization import (
    build_reliable_source_chapter_index,
    canonical_learning_material,
    is_displayable_source_chapter,
    normalize_content_title,
    resolve_source_chapter_title,
)


def version(kind: ContentVersionKind, title: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, title=title, content=content)


def bullet(title: str, marker: str, position: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        position=position,
        versions=[
            version(ContentVersionKind.ORIGINAL, title, f"{marker}原文"),
            version(ContentVersionKind.RECITATION, title, f"{marker}背诵"),
            version(ContentVersionKind.KEYWORDS, title, f"{marker}、关键词、记忆"),
        ],
        sources=[],
    )


def point(title: str, bullets: list[SimpleNamespace], position: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), title=title, position=position, bullet_points=bullets)


def chapter(title: str, points: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), title=title, position=0, knowledge_points=points)


class ContentOrganizationTests(unittest.TestCase):
    def test_real_cross_level_collision_keeps_the_complete_standalone_point(self) -> None:
        broad = point("人力资源基础概念", [
            bullet("人力资源的特征", "能动性和可再生性", 0),
            bullet("人力资源的含义", "基础概念其他内容", 1),
        ], 0)
        complete = point("人力资源的特征", [
            bullet(title, f"{title}完整说明", index)
            for index, title in enumerate(("能动性", "可再生性", "增值性", "时效性", "社会性"))
        ], 1)

        material = canonical_learning_material(uuid.uuid4(), [chapter("未分章", [broad, complete])])

        self.assertIsNone(material.chapters[0].title)
        self.assertFalse(material.chapters[0].chapter_resolved)
        by_title = {item.title: item for item in material.chapters[0].knowledge_points}
        self.assertEqual([item.versions[0].title for item in by_title["人力资源基础概念"].bullet_points], ["人力资源的含义"])
        self.assertEqual(
            [item.versions[0].title for item in by_title["人力资源的特征"].bullet_points],
            ["能动性", "可再生性", "增值性", "时效性", "社会性"],
        )
        all_internal_titles = [
            item.versions[0].title
            for knowledge in material.chapters[0].knowledge_points
            for item in knowledge.bullet_points
        ]
        self.assertNotIn("人力资源的特征", all_internal_titles)

    def test_exact_normalization_does_not_fuzzily_merge_similar_titles(self) -> None:
        self.assertEqual(normalize_content_title(" 1. 人力资源：特征 "), "人力资源:特征")
        self.assertEqual(normalize_content_title("人力资源：特征"), normalize_content_title("人力资源:特征"))
        self.assertNotEqual(normalize_content_title("人力资源的特征"), normalize_content_title("人力资源特征"))

    def test_more_complete_nested_bullet_merges_all_versions_without_repeating_identical_body(self) -> None:
        target_bullet = bullet("简述", "短", 0)
        target_bullet.versions[0].content = "共享正文"
        target_bullet.versions[1].content = "共享背诵"
        target_bullet.versions[2].content = "共享、关键词、记忆"
        incoming = bullet("目标知识点", "很长的补充内容用于证明嵌套条目明显更加完整", 0)
        incoming.versions[0].content = "共享正文\n\n更完整的原文内容"
        incoming.versions[1].content = "共享背诵\n\n更完整的背诵内容"
        incoming.versions[2].content = "共享、关键词、记忆、完整补充"
        broad = point("宽泛知识点", [incoming, bullet("其他内容", "必须保留", 1)], 0)
        target = point("目标知识点", [target_bullet], 1)

        material = canonical_learning_material(uuid.uuid4(), [chapter("可靠主题章节", [broad, target])])

        by_title = {item.title: item for item in material.chapters[0].knowledge_points}
        merged = by_title["目标知识点"].bullet_points[0]
        contents = {item.kind: item.content for item in merged.versions}
        self.assertEqual(contents[ContentVersionKind.ORIGINAL].count("共享正文"), 1)
        self.assertIn("更完整的原文内容", contents[ContentVersionKind.ORIGINAL])
        self.assertIn("更完整的背诵内容", contents[ContentVersionKind.RECITATION])
        self.assertIn("完整补充", contents[ContentVersionKind.KEYWORDS])
        self.assertEqual([item.versions[0].title for item in by_title["宽泛知识点"].bullet_points], ["其他内容"])

    def test_chapter_visibility_is_conservative_and_topic_specific(self) -> None:
        for title in ("未分章", "未归类内容", "人力.pdf · 第 1–292 页", "第 1–292 页", "第二章节", "第三章", "题纲父标题", "第一章世界的物质性及发展规律/ 25"):
            self.assertFalse(is_displayable_source_chapter(title), title)
        for title in ("第八章 员工关系管理", "第三章 工作分析", "招聘与甄选"):
            self.assertTrue(is_displayable_source_chapter(title), title)

    def test_propagated_heading_on_a_large_chunk_is_not_resolved(self) -> None:
        candidate = SimpleNamespace(
            chunk_id=uuid.uuid4(), chapter="第八章 员工关系管理", score=0.9,
            page_start=16, page_end=30, text="第八章 员工关系管理\n人力资源管理概述",
        )
        self.assertIsNone(resolve_source_chapter_title([candidate], [candidate.chunk_id]))

    def test_heading_only_directory_entry_is_not_resolved(self) -> None:
        candidate = SimpleNamespace(
            chunk_id=uuid.uuid4(), chapter="第六章 绩效管理", score=0.92,
            page_start=8, page_end=8, text="第六章 绩效管理",
        )
        self.assertIsNone(resolve_source_chapter_title([candidate], [candidate.chunk_id]))

    def test_real_textbook_body_boundaries_classify_middle_chapter_chunks(self) -> None:
        toc_one = SimpleNamespace(
            id=uuid.uuid4(), position=0, page_start=5, page_end=5,
            chapter_title="第一章世界的物质性及发展规律/ 25",
            content="第一章世界的物质性及发展规律/ 25",
        )
        toc_two = SimpleNamespace(
            id=uuid.uuid4(), position=1, page_start=6, page_end=6,
            chapter_title="第二章实践与认识及其发展规律/ 69",
            content="第二章实践与认识及其发展规律/ 69",
        )
        first_heading = SimpleNamespace(
            id=uuid.uuid4(), position=10, page_start=33, page_end=34,
            chapter_title="第一章世界的物质性及发展规律",
            content="第一章世界的物质性及发展规律\n学习目标与本章正文内容足够长，用于确认这是真实正文首页而不是目录。",
        )
        first_middle = SimpleNamespace(
            id=uuid.uuid4(), position=11, page_start=40, page_end=41,
            chapter_title="第一章世界的物质性及发展规律",
            content="本章中间正文不会重复印刷完整章名，但仍然属于第一章。",
        )
        second_heading = SimpleNamespace(
            id=uuid.uuid4(), position=20, page_start=77, page_end=78,
            chapter_title="第二章实践与认识及其发展规律",
            content="第二章实践与认识及其发展规律\n学习目标与本章正文内容足够长，用于确认第二章真实正文边界。",
        )
        second_middle = SimpleNamespace(
            id=uuid.uuid4(), position=21, page_start=90, page_end=91,
            chapter_title="第二章实践与认识及其发展规律",
            content="实践与认识的中间正文内容，不需要再次重复章名。",
        )
        index = build_reliable_source_chapter_index([
            toc_one, toc_two, first_heading, first_middle, second_heading, second_middle,
        ])
        self.assertNotIn(toc_one.id, index)
        self.assertEqual(index[first_middle.id], "第一章世界的物质性及发展规律")
        self.assertEqual(index[second_middle.id], "第二章实践与认识及其发展规律")

        candidate = SimpleNamespace(
            chunk_id=second_middle.id,
            chapter=second_middle.chapter_title,
            score=0.94,
            page_start=90,
            page_end=91,
            text=second_middle.content,
        )
        self.assertIsNone(resolve_source_chapter_title([candidate], [candidate.chunk_id]))
        self.assertEqual(
            resolve_source_chapter_title([candidate], [candidate.chunk_id], index),
            "第二章实践与认识及其发展规律",
        )

    def test_toc_propagation_does_not_create_a_reliable_body_boundary(self) -> None:
        toc = SimpleNamespace(
            id=uuid.uuid4(), position=0, page_start=8, page_end=8,
            chapter_title="第八章员工关系管理",
            content=(
                "第八章员工关系管理\n"
                "第一章人力资源管理概述\n第二章工作分析\n第三章招聘与甄选"
            ),
        )
        propagated = SimpleNamespace(
            id=uuid.uuid4(), position=1, page_start=100, page_end=101,
            chapter_title="第八章员工关系管理",
            content="普通正文内容，章名只是由目录最后一项错误向后传播。",
        )
        self.assertEqual(build_reliable_source_chapter_index([toc, propagated]), {})


if __name__ == "__main__":
    unittest.main()
