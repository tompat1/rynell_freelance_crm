from __future__ import annotations
from sqlalchemy import func

import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from .db import create_db_and_tables, get_session, DATA_DIR
from .models import Activity, Asset, Company, Contact, Event, Idea, Lead, Project, Task

app = FastAPI(title="Freelance CRM (MVP)")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_ASSET_MIME_TYPES = {
    "application/pdf",
    "application/postscript",
    "application/vnd.adobe.illustrator",
    "application/vnd.sketch",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
    "video/mp4",
}
ALLOWED_ASSET_EXTENSIONS = {
    ".ai",
    ".eps",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".pdf",
    ".png",
    ".psd",
    ".sketch",
    ".svg",
    ".webp",
}

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # store naive UTC in SQLite

def parse_optional_datetime(value: str) -> Optional[datetime]:
    if not value or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

def parse_optional_float(value: str) -> Optional[float]:
    if not value or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None

def add_activity(session, action: str, entity_type: str, entity_id: Optional[int], summary: str, changes: Optional[dict] = None):
    session.add(Activity(action=action, entity_type=entity_type, entity_id=entity_id, summary=summary, changes=changes))
    session.commit()

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

def session_dep():
    session = get_session()
    try:
        yield session
    finally:
        session.close()

# ---------- Dashboard ----------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session=Depends(session_dep)):
    counts = {
        "contacts": session.exec(select(func.count()).select_from(Contact)).one(),
        "companies": session.exec(select(func.count()).select_from(Company)).one(),
        "leads": session.exec(select(func.count()).select_from(Lead)).one(),
        "ideas": session.exec(select(func.count()).select_from(Idea)).one(),
        "projects": session.exec(select(func.count()).select_from(Project)).one(),
        "assets": session.exec(select(func.count()).select_from(Asset)).one(),
        "tasks_open": session.exec(
            select(func.count()).select_from(Task).where(Task.status != "DONE")
        ).one(),
    }
    recent = session.exec(select(Activity).order_by(Activity.ts.desc()).limit(20)).all()
    return templates.TemplateResponse("dashboard.html", {"request": request, "counts": counts, "recent": recent})

# ---------- Contacts ----------
@app.get("/contacts", response_class=HTMLResponse)
def contacts_list(request: Request, q: str = "", session=Depends(session_dep)):
    stmt = select(Contact)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Contact.first_name.like(like)) | (Contact.last_name.like(like)) | (Contact.email.like(like)))
    contacts = session.exec(stmt.order_by(Contact.last_name, Contact.first_name)).all()
    companies = session.exec(select(Company).order_by(Company.name)).all()
    return templates.TemplateResponse("contacts.html", {"request": request, "contacts": contacts, "companies": companies, "q": q})

@app.post("/contacts")
def contacts_create(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    role: str = Form(""),
    company_id: Optional[int] = Form(None),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    c = Contact(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=(email.strip() or None),
        phone=(phone.strip() or None),
        role=(role.strip() or None),
        company_id=company_id,
        notes=(notes.strip() or None),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    add_activity(session, "CREATE", "Contact", c.id, f"Created contact: {c.first_name} {c.last_name}")
    return RedirectResponse(url=f"/contacts/{c.id}", status_code=303)

@app.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contacts_detail(request: Request, contact_id: int, session=Depends(session_dep)):
    contact = session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    company = session.get(Company, contact.company_id) if contact.company_id else None
    leads = session.exec(select(Lead).where(Lead.contact_id == contact_id).order_by(Lead.updated_at.desc())).all()
    projects = session.exec(select(Project).where(Project.contact_id == contact_id).order_by(Project.updated_at.desc())).all()
    assets = session.exec(select(Asset).where(Asset.contact_id == contact_id).order_by(Asset.created_at.desc())).all()
    activity = session.exec(select(Activity).where(Activity.entity_type == "Contact", Activity.entity_id == contact_id).order_by(Activity.ts.desc()).limit(50)).all()
    companies = session.exec(select(Company).order_by(Company.name)).all()
    return templates.TemplateResponse("contact_detail.html", {
        "request": request,
        "contact": contact,
        "company": company,
        "companies": companies,
        "leads": leads,
        "projects": projects,
        "assets": assets,
        "activity": activity,
    })

@app.post("/contacts/{contact_id}/update")
def contacts_update(
    contact_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    role: str = Form(""),
    company_id: Optional[int] = Form(None),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "Contact not found")
    before = {"first_name": c.first_name, "last_name": c.last_name, "email": c.email, "phone": c.phone, "role": c.role, "company_id": c.company_id, "notes": c.notes}
    c.first_name = first_name.strip()
    c.last_name = last_name.strip()
    c.email = (email.strip() or None)
    c.phone = (phone.strip() or None)
    c.role = (role.strip() or None)
    c.company_id = company_id
    c.notes = (notes.strip() or None)
    c.updated_at = now_utc()
    session.add(c)
    session.commit()
    after = {"first_name": c.first_name, "last_name": c.last_name, "email": c.email, "phone": c.phone, "role": c.role, "company_id": c.company_id, "notes": c.notes}
    changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changes:
        add_activity(session, "UPDATE", "Contact", c.id, f"Updated contact: {c.first_name} {c.last_name}", changes=changes)
    return RedirectResponse(url=f"/contacts/{c.id}", status_code=303)

# ---------- Companies ----------
@app.get("/companies", response_class=HTMLResponse)
def companies_list(request: Request, session=Depends(session_dep)):
    companies = session.exec(select(Company).order_by(Company.name)).all()
    return templates.TemplateResponse("companies.html", {"request": request, "companies": companies})

@app.post("/companies")
def companies_create(
    name: str = Form(...),
    website: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    comp = Company(name=name.strip(), website=(website.strip() or None), notes=(notes.strip() or None), created_at=now_utc(), updated_at=now_utc())
    session.add(comp)
    session.commit()
    session.refresh(comp)
    add_activity(session, "CREATE", "Company", comp.id, f"Created company: {comp.name}")
    return RedirectResponse(url="/companies", status_code=303)

# ---------- Leads ----------
LEAD_STATUSES = ["NEW", "CONTACTED", "QUALIFIED", "PROPOSAL", "WON", "LOST"]

@app.get("/leads", response_class=HTMLResponse)
def leads_board(request: Request, session=Depends(session_dep)):
    leads = session.exec(select(Lead).order_by(Lead.updated_at.desc())).all()
    companies = session.exec(select(Company).order_by(Company.name)).all()
    contacts = session.exec(select(Contact).order_by(Contact.last_name, Contact.first_name)).all()
    columns = {s: [] for s in LEAD_STATUSES}
    for l in leads:
        columns.setdefault(l.status, []).append(l)
    return templates.TemplateResponse("leads.html", {"request": request, "columns": columns, "companies": companies, "contacts": contacts, "statuses": LEAD_STATUSES})

@app.post("/leads")
def leads_create(
    title: str = Form(...),
    status: str = Form("NEW"),
    source: str = Form(""),
    value_estimate: str = Form(""),
    company_id: Optional[int] = Form(None),
    contact_id: Optional[int] = Form(None),
    next_step: str = Form(""),
    due_date: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if status not in LEAD_STATUSES:
        status = "NEW"
    ve = parse_optional_float(value_estimate)
    dd = parse_optional_datetime(due_date)
    lead = Lead(
        title=title.strip(),
        status=status,
        source=(source.strip() or None),
        value_estimate=ve,
        company_id=company_id,
        contact_id=contact_id,
        next_step=(next_step.strip() or None),
        due_date=dd,
        notes=(notes.strip() or None),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    add_activity(session, "CREATE", "Lead", lead.id, f"Created lead: {lead.title} ({lead.status})")
    return RedirectResponse(url="/leads", status_code=303)

@app.post("/leads/{lead_id}/status")
def leads_set_status(lead_id: int, status: str = Form(...), session=Depends(session_dep)):
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if status not in LEAD_STATUSES:
        raise HTTPException(400, "Invalid status")
    before = lead.status
    if before != status:
        lead.status = status
        lead.updated_at = now_utc()
        session.add(lead)
        session.commit()
        add_activity(session, "STATUS", "Lead", lead.id, f"Lead moved: {lead.title}", changes={"status": {"from": before, "to": status}})
    return RedirectResponse(url="/leads", status_code=303)

# ---------- Ideas ----------
IDEA_STATUSES = ["BACKLOG", "IN_PROGRESS", "PARKED", "DONE"]

@app.get("/ideas", response_class=HTMLResponse)
def ideas_list(request: Request, session=Depends(session_dep)):
    ideas = session.exec(select(Idea).order_by(Idea.updated_at.desc())).all()
    return templates.TemplateResponse("ideas.html", {"request": request, "ideas": ideas, "statuses": IDEA_STATUSES})

@app.post("/ideas")
def ideas_create(
    title: str = Form(...),
    status: str = Form("BACKLOG"),
    tags: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if status not in IDEA_STATUSES:
        status = "BACKLOG"
    idea = Idea(title=title.strip(), status=status, tags=(tags.strip() or None), notes=(notes.strip() or None),
                created_at=now_utc(), updated_at=now_utc())
    session.add(idea)
    session.commit()
    session.refresh(idea)
    add_activity(session, "CREATE", "Idea", idea.id, f"Created idea: {idea.title}")
    return RedirectResponse(url="/ideas", status_code=303)

# ---------- Projects ----------
PROJECT_STATUSES = ["ACTIVE", "ON_HOLD", "DONE", "ARCHIVED"]

@app.get("/projects", response_class=HTMLResponse)
def projects_list(request: Request, session=Depends(session_dep)):
    projects = session.exec(select(Project).order_by(Project.updated_at.desc())).all()
    companies = session.exec(select(Company).order_by(Company.name)).all()
    contacts = session.exec(select(Contact).order_by(Contact.last_name, Contact.first_name)).all()
    return templates.TemplateResponse("projects.html", {"request": request, "projects": projects, "companies": companies, "contacts": contacts, "statuses": PROJECT_STATUSES})

@app.post("/projects")
def projects_create(
    name: str = Form(...),
    status: str = Form("ACTIVE"),
    company_id: Optional[int] = Form(None),
    contact_id: Optional[int] = Form(None),
    start_date: str = Form(""),
    end_date: str = Form(""),
    budget: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if status not in PROJECT_STATUSES:
        status = "ACTIVE"
    sd = parse_optional_datetime(start_date)
    ed = parse_optional_datetime(end_date)
    b = parse_optional_float(budget)
    p = Project(
        name=name.strip(),
        status=status,
        company_id=company_id,
        contact_id=contact_id,
        start_date=sd,
        end_date=ed,
        budget=b,
        notes=(notes.strip() or None),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    add_activity(session, "CREATE", "Project", p.id, f"Created project: {p.name}")
    return RedirectResponse(url=f"/projects/{p.id}", status_code=303)

@app.get("/projects/{project_id}", response_class=HTMLResponse)
def projects_detail(request: Request, project_id: int, session=Depends(session_dep)):
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    company = session.get(Company, p.company_id) if p.company_id else None
    contact = session.get(Contact, p.contact_id) if p.contact_id else None
    tasks = session.exec(select(Task).where(Task.project_id == project_id).order_by(Task.due_date.is_(None), Task.due_date.asc())).all()
    assets = session.exec(select(Asset).where(Asset.project_id == project_id).order_by(Asset.created_at.desc())).all()
    events = session.exec(select(Event).where(Event.project_id == project_id).order_by(Event.start.desc())).all()
    activity = session.exec(select(Activity).where(Activity.entity_type == "Project", Activity.entity_id == project_id).order_by(Activity.ts.desc()).limit(100)).all()
    return templates.TemplateResponse("project_detail.html", {
        "request": request,
        "project": p,
        "company": company,
        "contact": contact,
        "tasks": tasks,
        "assets": assets,
        "events": events,
        "activity": activity,
        "statuses": PROJECT_STATUSES,
    })

@app.post("/projects/{project_id}/status")
def projects_set_status(project_id: int, status: str = Form(...), session=Depends(session_dep)):
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    if status not in PROJECT_STATUSES:
        raise HTTPException(400, "Invalid status")
    before = p.status
    if before != status:
        p.status = status
        p.updated_at = now_utc()
        session.add(p)
        session.commit()
        add_activity(session, "STATUS", "Project", p.id, f"Project status changed: {p.name}", changes={"status": {"from": before, "to": status}})
    return RedirectResponse(url=f"/projects/{p.id}", status_code=303)

# ---------- Tasks ----------
TASK_STATUSES = ["TODO", "DOING", "BLOCKED", "DONE"]

@app.post("/projects/{project_id}/tasks")
def tasks_create(
    project_id: int,
    title: str = Form(...),
    due_date: str = Form(""),
    status: str = Form("TODO"),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if status not in TASK_STATUSES:
        status = "TODO"
    dd = parse_optional_datetime(due_date)
    t = Task(project_id=project_id, title=title.strip(), status=status, due_date=dd, notes=(notes.strip() or None),
             created_at=now_utc(), updated_at=now_utc())
    session.add(t)
    session.commit()
    session.refresh(t)
    add_activity(session, "CREATE", "Task", t.id, f"Created task: {t.title}", changes={"project_id": project_id})
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/tasks/{task_id}/status")
def tasks_set_status(task_id: int, status: str = Form(...), session=Depends(session_dep)):
    t = session.get(Task, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    if status not in TASK_STATUSES:
        raise HTTPException(400, "Invalid status")
    before = t.status
    if before != status:
        t.status = status
        t.updated_at = now_utc()
        session.add(t)
        session.commit()
        add_activity(session, "STATUS", "Task", t.id, f"Task status changed: {t.title}", changes={"status": {"from": before, "to": status}})
    return RedirectResponse(url=f"/projects/{t.project_id}", status_code=303)

# ---------- Events / Calendar ----------
@app.get("/calendar", response_class=HTMLResponse)
def calendar_view(request: Request, session=Depends(session_dep)):
    projects = session.exec(select(Project).order_by(Project.name)).all()
    contacts = session.exec(select(Contact).order_by(Contact.last_name, Contact.first_name)).all()
    return templates.TemplateResponse("calendar.html", {"request": request, "projects": projects, "contacts": contacts})

@app.post("/events")
def events_create(
    title: str = Form(...),
    start: str = Form(...),
    end: str = Form(""),
    all_day: Optional[bool] = Form(False),
    project_id: Optional[int] = Form(None),
    contact_id: Optional[int] = Form(None),
    location: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    try:
        start_dt = datetime.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "Invalid start datetime")
    end_dt = None
    if end.strip():
        try:
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            end_dt = None
    e = Event(
        title=title.strip(),
        start=start_dt,
        end=end_dt,
        all_day=bool(all_day),
        project_id=project_id,
        contact_id=contact_id,
        location=(location.strip() or None),
        notes=(notes.strip() or None),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(e)
    session.commit()
    session.refresh(e)
    add_activity(session, "CREATE", "Event", e.id, f"Created event: {e.title}")
    return RedirectResponse(url="/calendar", status_code=303)

@app.get("/api/calendar")
def calendar_feed(session=Depends(session_dep)):
    # FullCalendar expects: id, title, start, end, allDay
    events = session.exec(select(Event)).all()
    tasks = session.exec(select(Task).where(Task.due_date.is_not(None), Task.status != "DONE")).all()
    payload = []
    for e in events:
        payload.append({
            "id": f"event-{e.id}",
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat() if e.end else None,
            "allDay": bool(e.all_day),
            "extendedProps": {"type": "event", "entityId": e.id},
        })
    for t in tasks:
        payload.append({
            "id": f"task-{t.id}",
            "title": f"📝 {t.title}",
            "start": t.due_date.isoformat(),
            "end": None,
            "allDay": True,
            "extendedProps": {"type": "task", "entityId": t.id, "projectId": t.project_id},
        })
    return JSONResponse(payload)

# ---------- Assets ----------
@app.get("/assets", response_class=HTMLResponse)
def assets_list(request: Request, session=Depends(session_dep)):
    assets = session.exec(select(Asset).order_by(Asset.created_at.desc()).limit(200)).all()
    projects = session.exec(select(Project).order_by(Project.name)).all()
    contacts = session.exec(select(Contact).order_by(Contact.last_name, Contact.first_name)).all()
    return templates.TemplateResponse("assets.html", {"request": request, "assets": assets, "projects": projects, "contacts": contacts})

@app.post("/assets/upload")
async def assets_upload(
    file: UploadFile = File(...),
    tags: str = Form(""),
    project_id: Optional[int] = Form(None),
    contact_id: Optional[int] = Form(None),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if not file.filename:
        raise HTTPException(400, "No filename")
    safe_name = os.path.basename(file.filename)
    token = uuid.uuid4().hex
    stored_name = f"{token}_{safe_name}"
    stored_path = UPLOAD_DIR / stored_name

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")

    mime = file.content_type or mimetypes.guess_type(safe_name)[0]
    ext = Path(safe_name).suffix.lower()
    if mime not in ALLOWED_ASSET_MIME_TYPES and ext not in ALLOWED_ASSET_EXTENSIONS:
        raise HTTPException(400, "Unsupported file type")

    stored_path.write_bytes(content)

    a = Asset(
        filename=safe_name,
        stored_path=str(stored_name),
        mime_type=mime,
        size_bytes=len(content),
        tags=(tags.strip() or None),
        project_id=project_id,
        contact_id=contact_id,
        notes=(notes.strip() or None),
        created_at=now_utc(),
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    add_activity(session, "UPLOAD", "Asset", a.id, f"Uploaded asset: {a.filename}", changes={"size_bytes": a.size_bytes, "mime_type": a.mime_type})
    return RedirectResponse(url="/assets", status_code=303)

# ---------- Activity ----------
@app.get("/activity", response_class=HTMLResponse)
def activity_feed(request: Request, session=Depends(session_dep)):
    items = session.exec(select(Activity).order_by(Activity.ts.desc()).limit(300)).all()
    return templates.TemplateResponse("activity.html", {"request": request, "items": items})
