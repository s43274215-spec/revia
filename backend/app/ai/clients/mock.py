import json
import re
import uuid

from app.ai.clients.base import AIClient


class MockAIClient(AIClient):
    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        if "REVIA_QUERY_REWRITE_V1" in user_prompt:
            return '{"queries":[]}'
        if "REVIA_ITEM_V2" in user_prompt:
            return self._generate_item(user_prompt)
        project_id = self._project_id_from(user_prompt)
        chapter_id = uuid.uuid5(project_id, "chapter-market-failure")
        knowledge_point_id = uuid.uuid5(project_id, "knowledge-point-externality")
        bullet_point_id = uuid.uuid5(project_id, "bullet-point-externality-definition")
        payload = {
            "project_id": str(project_id),
            "chapters": [
                {
                    "id": str(chapter_id),
                    "title": "市场失灵与政府干预",
                    "knowledge_points": [
                        {
                            "id": str(knowledge_point_id),
                            "title": "外部性",
                            "bullet_points": [
                                {
                                    "id": str(bullet_point_id),
                                    "versions": [
                                        {
                                            "kind": "original",
                                            "title": "外部性的定义",
                                            "content": "外部性是一个经济主体的行为对其他主体产生、但未通过市场价格反映的影响。",
                                        },
                                        {
                                            "kind": "recitation",
                                            "title": "外部性的定义",
                                            "content": "外部性是经济活动对第三方造成且未被价格机制计入的影响。",
                                        },
                                        {
                                            "kind": "keywords",
                                            "title": "外部性的定义",
                                            "content": "第三方影响；价格机制之外；正外部性与负外部性",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _generate_item(prompt: str) -> str:
        item_match = re.search(r"当前考纲条目：(.+)", prompt)
        context_match = re.search(
            r"SOURCE_CONTEXT_JSON_START\s*(\[.*?\])\s*SOURCE_CONTEXT_JSON_END",
            prompt,
            flags=re.DOTALL,
        )
        if not item_match or not context_match:
            raise ValueError("Mock item prompt is missing its syllabus item or source context")
        item = item_match.group(1).strip()
        knowledge_title = re.split(r"[：:]", item, maxsplit=1)[0].strip()[:25]
        candidates = json.loads(context_match.group(1))
        if not candidates:
            raise ValueError("Mock item prompt requires at least one candidate chunk")
        source = candidates[0]
        source_text = str(source["text"]).strip()
        bullet_title = "核心内容"
        payload = {
            "knowledge_point_title": knowledge_title,
            "bullet_points": [
                {
                    "title": bullet_title,
                    "original": {"title": bullet_title, "content": source_text},
                    "recitation": {
                        "title": bullet_title,
                        "content": f"围绕{item}，依据资料概括为：{source_text}",
                    },
                    "keywords": {"title": bullet_title, "content": f"{knowledge_title}、核心概念、资料依据"},
                    "source_chunk_ids": [str(source["chunk_id"])],
                    "source_pages": [int(source["page_start"])],
                }
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _project_id_from(prompt: str) -> uuid.UUID:
        match = re.search(r"项目 ID：([0-9a-fA-F-]{36})", prompt)
        if not match:
            raise ValueError("Mock prompt does not contain a valid project ID")
        return uuid.UUID(match.group(1))
