# Final product polish deployment notes

## Workspace session configuration

The browser now receives only an HttpOnly signed session cookie. The Next.js backend proxy reads `REVIA_API_BASE_URL`; access codes and Workspace IDs remain backend-only.

Required backend production variables:

- `OWNER_ACCESS_CODE` (preferred) or the compatible existing `APP_ACCESS_CODE`
- `OWNER_WORKSPACE_ID`
- `DEMO_ACCESS_CODE`
- `DEMO_WORKSPACE_ID`
- `SESSION_SIGNING_KEY` (existing name; at least 32 random bytes)
- `SESSION_MAX_AGE_SECONDS` (optional; defaults to 30 days)

Required Vercel variable:

- `REVIA_API_BASE_URL`, for example `https://your-render-service/api/v1`

Do not use `NEXT_PUBLIC_*` for access codes, Workspace IDs, or signing keys.

To locate the owner Workspace without changing production data, run this read-only query using a separately authorized read-only database connection:

```sql
SELECT id, workspace_id
FROM projects
WHERE id IN (
  '03ffb29f-b450-4fc2-9cb2-d6525cb9aaf5',
  '3614d82d-1138-4ae0-a2f2-2417836aca37'
)
ORDER BY id;
```

Both rows must exist and must have the same `workspace_id`. Configure that value as `OWNER_WORKSPACE_ID`; do not copy it into source code.

## Demo Workspace

`backend/scripts/create_demo_workspace.py` is dry-run by default and never copies production data. From the backend directory:

```powershell
python scripts/create_demo_workspace.py
```

Only an operator who intends to write the selected database should use `--apply`. Review and add purpose-built demo content separately before setting `DEMO_WORKSPACE_ID`. Demo sessions are enforced read-only for project creation/update/deletion, uploads, syllabus writes, OCR/processing, generation, content deletion, and settings. Word export remains available.

## Source Chapter compatibility

New PDF parsing uses source structure in this order:

1. top-level PDF bookmarks/table of contents;
2. detected chapter headings and page ranges;
3. saved `TextChunk.chapter_title` metadata;
4. deterministic source-filename and evidence page ranges such as `课程资料 · 第 42–48 页`;
5. `未归类内容`.

Syllabus parent titles are never used as Source Chapter fallbacks. Existing projects are not rewritten. A later learning-material regeneration can apply Source Chapter grouping from already saved chunks; it does not require PDF parsing when those chunks contain usable `chapter_title` values. If they do not, grouping still uses evidence page ranges without re-OCR.
