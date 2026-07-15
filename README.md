# Revia

Revia 是一个以连续阅读、可编辑知识和渐进复习为核心的学习 Web App。前端使用 Next.js，后端使用 FastAPI、SQLAlchemy、Alembic 和 PostgreSQL。

## 本地开发

复制前后端环境变量示例并填写本地值：

```powershell
Copy-Item .env.example .env.local
Copy-Item backend/.env.example backend/.env
```

后端的 `APP_ACCESS_CODE` 是进入本地匿名工作区使用的访问码。随后在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-revia.ps1
```

停止本地服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-revia.ps1
```

## 数据库迁移

Revia 使用 Alembic 作为开发与生产的唯一建表、升级入口，不在应用启动事件中调用 `create_all()`。

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

查看当前版本：

```powershell
.\.venv\Scripts\python.exe -m alembic current
```

## Neon PostgreSQL

1. 在 Neon 创建免费项目和数据库。
2. 复制 Neon 提供的连接地址。
3. 将连接地址配置为 Render 的 `DATABASE_URL`。代码会自动把 `postgresql://` 或 `postgres://` 转换为 SQLAlchemy 的 `postgresql+psycopg://`。
4. Render 启动命令会先执行 `alembic upgrade head`，再启动 API。

不要把连接地址写入 `.env.example`、源码或 Git。

## Render 后端

仓库根目录提供了 `render.yaml`。手动创建时使用以下配置：

- Root Directory：`backend`
- Build Command：`pip install -r requirements.txt`
- Start Command：`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check：`/health`
- Instance：Free

必须配置：

```text
ENVIRONMENT=production
DATABASE_URL=<Neon PostgreSQL URL>
CORS_ORIGINS=["https://<your-vercel-project>.vercel.app"]
APP_ACCESS_CODE=<private access code>
SESSION_SIGNING_KEY=<at least 32 random bytes>
CREDENTIAL_ENCRYPTION_KEY=<Fernet key>
AI_MODE=live
AI_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
PYTHON_VERSION=3.12.13
FILE_STORAGE_ROOT=/tmp/revia
AI_TIMEOUT_SECONDS=60
AI_MAX_OUTPUT_TOKENS=4096
AI_TEMPERATURE=0.1
MATCHING_THRESHOLD=0.35
MATCHING_MAX_CANDIDATES=3
OCR_ENABLED=true
OCR_DPI=144
OCR_MINIMUM_TEXT_LENGTH=8
MAX_UPLOAD_MB=25
MAX_PDF_PAGES=120
```

生产配置缺失、仍指向本地数据库、仍使用 Mock AI 或仍包含本地 CORS 时，后端会明确拒绝启动。

生成服务端密钥：

```powershell
# SESSION_SIGNING_KEY
cd backend
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"

# CREDENTIAL_ENCRYPTION_KEY
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

访问码通过后，后端签发带签名的匿名工作区 Token。每个工作区的 DeepSeek Key 使用 `CREDENTIAL_ENCRYPTION_KEY` 加密后保存在 PostgreSQL，Render 重启不会丢失。

## Vercel 前端

Vercel 使用仓库根目录，Next.js 默认构建命令即可。配置 Production 环境变量：

```text
NEXT_PUBLIC_API_BASE_URL=https://<your-render-service>.onrender.com/api/v1
```

首次部署拿到稳定的 Vercel 默认域名后，将其精确写入 Render 的 `CORS_ORIGINS` JSON 数组并重新部署后端。浏览器请求会携带 `Authorization`，后端 CORS 已允许该请求头。

## 免费部署限制

- Render 免费 Web Service 会休眠，首次访问可能需要等待唤醒。
- PDF 最大 25MB、120 页，只作为解析期间的临时文件。
- 解析成功并保存 TextChunk 后立即删除 PDF；解析失败也会清理，因此不能依赖原文件重新解析。
- OCR 采用 RapidOCR 和 ONNX Runtime CPU，按页处理。部署后先做小文件冒烟测试，再做 100 页真实压力测试。
- 第一版不使用对象存储。

## 安全边界

- 不在浏览器、日志或 API 响应中保存或返回完整 DeepSeek Key。
- 不把 `.env`、PDF、本地数据库、日志、`.secrets`、测试截图或 Playwright 结果提交到 Git。
- CORS 不是身份认证；所有业务 API 同时要求匿名工作区 Bearer Token。
