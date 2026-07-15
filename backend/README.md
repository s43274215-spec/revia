# Revia FastAPI Backend

后端提供项目、PDF 解析、考纲匹配、DeepSeek 生成、三版本学习材料和匿名工作区设置 API。

## 数据归属

```text
Workspace
├── role: owner / public
├── DeepSeekCredential
└── Project
    ├── Document → DocumentPage → ParsedDocument → ParsedPage / TextChunk
    ├── Syllabus
    ├── GenerationJob
    └── Chapter → KnowledgePoint → BulletPoint → ContentVersion

SiteSettings（单例）保存运行时公开状态和最后修改者。`APP_ACCESS_CODE` 登录始终返回唯一 Owner Workspace；普通工作区不能修改站点配置。
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

生产环境由浏览器使用短期 presigned URL 将完整 PDF 直传私有 S3 兼容对象存储，本地开发使用统一接口的本地存储实现。PyMuPDF 逐页读取文本层；只有缺少有效文本层的页面才由 RapidOCR 和 ONNX Runtime CPU 识别。每个 `DocumentPage` 完成后立即提交，默认限制为 150MB、600 页。

中断任务通过数据库租约接管，从首个未完成页面继续；已完成页面不会再次 OCR。全部页面完成后才按页码重建连续 `ParsedDocument`，继续使用现有 TextStructurer 和 StructuredTextSplitter，因此执行检查点和页面边界不会成为 TextChunk 边界。TextChunk 提交成功后删除对象存储原文件，未完成任务保留对象用于恢复。持久化队列按接受时间排序，全站默认同时只解析一份文档。

## 数据库迁移

开发与生产统一使用：

```bash
alembic upgrade head
```

应用启动时不会调用 `create_all()`，也没有运行时手写 `ALTER TABLE`。

完整的 Neon、Render、Vercel 配置和密钥生成方式见仓库根目录 `README.md`。
