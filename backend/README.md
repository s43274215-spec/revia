# Revia FastAPI Backend

后端提供项目、PDF 解析、考纲匹配、DeepSeek 生成、三版本学习材料和匿名工作区设置 API。

## 数据归属

```text
Workspace
├── DeepSeekCredential
└── Project
    ├── Document → ParsedDocument → ParsedPage / TextChunk
    ├── Syllabus
    ├── GenerationJob
    └── Chapter → KnowledgePoint → BulletPoint → ContentVersion
```

所有业务 API 都要求 `Authorization: Bearer <workspace-token>`。`Project.workspace_id` 是项目及其全部下游数据的唯一归属来源；跨工作区查询返回 404。DeepSeek Key 按 workspace 单独使用 Fernet 认证加密后存入 PostgreSQL，主密钥只来自 `CREDENTIAL_ENCRYPTION_KEY`。

## 本地运行

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：`GET /health`

## 文档处理

PyMuPDF 首先读取文本层；扫描页由 RapidOCR 和 ONNX Runtime CPU 逐页识别。默认限制为 25MB、120 页。PDF 仅作为临时解析文件，TextChunk 提交成功或解析失败后都会清理。

## 数据库迁移

开发与生产统一使用：

```bash
alembic upgrade head
```

应用启动时不会调用 `create_all()`，也没有运行时手写 `ALTER TABLE`。

完整的 Neon、Render、Vercel 配置和密钥生成方式见仓库根目录 `README.md`。
