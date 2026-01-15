# Freelance CRM (MVP) — Contacts • Leads • Ideas • Projects • Assets • Calendar • History

This is a **single-user** starter CRM for freelance Digital Design work.

It includes:
- **Contacts + Companies**
- **Leads** (pipeline statuses)
- **Ideas** (backlog / inspiration)
- **Projects + Tasks**
- **Assets** (file uploads + linking to projects/contacts)
- **Calendar** (events + task due dates)
- **History / Activity feed** (tracks “when you did what”)

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Open: http://127.0.0.1:8000

## Notes
- Uses **SQLite** DB stored at `app/data/crm.db`
- Uploaded files stored at `app/data/uploads/` and served from `/uploads`
- Asset uploads are limited to 25 MB and restricted to common design file types (by MIME type or extension).
- No authentication yet (intended for local use while iterating). Add auth before exposing publicly.
