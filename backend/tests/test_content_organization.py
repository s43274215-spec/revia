import unittest
import uuid
from types import SimpleNamespace

from app.models.enums import ContentVersionKind
from app.services.content_organization import (
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
        for title in ("未分章", "未归类内容", "人力.pdf · 第 1–292 页", "第 1–292 页", "第二章节", "第三章", "题纲父标题"):
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


if __name__ == "__main__":
    unittest.main()
