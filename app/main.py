from __future__ import annotations
from sqlalchemy import and_, func, or_

import csv
import io
import mimetypes
import os
import re
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

def parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def add_activity(session, action: str, entity_type: str, entity_id: Optional[int], summary: str, changes: Optional[dict] = None):
    session.add(Activity(action=action, entity_type=entity_type, entity_id=entity_id, summary=summary, changes=changes))
    session.commit()

def extract_emails(raw: str) -> list[str]:
    if not raw:
        return []
    email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    emails: list[str] = []
    for candidate in re.split(r"[;\s,]+", raw):
        cleaned = candidate.strip()
        if not cleaned:
            continue
        if cleaned.lower().startswith("mailto:"):
            cleaned = cleaned[7:]
        if email_regex.match(cleaned):
            emails.append(cleaned)
    return emails

def name_from_email(email: str) -> tuple[str, str]:
    local = email.split("@", 1)[0]
    parts = [part for part in re.split(r"[._\-]+", local) if part]
    if not parts:
        return "", ""
    first = parts[0].replace("+", " ").title()
    last = " ".join(part.replace("+", " ").title() for part in parts[1:])
    return first, last

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
    imported = request.query_params.get("imported")
    skipped = request.query_params.get("skipped")
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "contacts.html",
        {
            "request": request,
            "contacts": contacts,
            "companies": companies,
            "q": q,
            "imported": imported,
            "skipped": skipped,
            "error": error,
        },
    )

@app.post("/contacts")
def contacts_create(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    role: str = Form(""),
    company_id: Optional[str] = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    company_id_value = parse_optional_int(company_id)
    c = Contact(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=(email.strip() or None),
        phone=(phone.strip() or None),
        role=(role.strip() or None),
        company_id=company_id_value,
        notes=(notes.strip() or None),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    add_activity(session, "CREATE", "Contact", c.id, f"Created contact: {c.first_name} {c.last_name}")
    return RedirectResponse(url=f"/contacts/{c.id}", status_code=303)

@app.post("/contacts/import")
async def contacts_import(file: Optional[UploadFile] = File(None), session=Depends(session_dep)):
    if not file or not file.filename:
        return RedirectResponse(url="/contacts?error=missing_csv", status_code=303)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV file is missing a header row")
    fieldnames = {name.lower().strip(): name for name in reader.fieldnames if name}

    def get_value(row: dict, *keys: str) -> str:
        for key in keys:
            column = fieldnames.get(key)
            if not column:
                continue
            value = row.get(column)
            if value is None:
                continue
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
        return ""

    imported = 0
    skipped = 0
    for row in reader:
        first_name = get_value(row, "first_name", "first name", "first")
        last_name = get_value(row, "last_name", "last name", "last")
        full_name = get_value(row, "full_name", "full name", "name", "magazine", "publication")
        email = get_value(row, "email", "email_address", "email address")
        emails_value = get_value(row, "emails", "email_list", "email list")
        emails = [email] if email else extract_emails(emails_value)
        if full_name and (not first_name or not last_name):
            name_parts = full_name.split()
            if not first_name:
                first_name = name_parts[0] if name_parts else full_name
            if not last_name:
                last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        if not emails and not first_name and not last_name:
            skipped += 1
            continue
        phone = get_value(row, "phone", "phone_number", "phone number")
        role = get_value(row, "role", "title")
        notes = get_value(row, "notes", "note")
        site_name = get_value(row, "site", "website", "url")
        company_name = get_value(row, "company", "company_name", "company name")
        if not company_name and site_name:
            company_name = site_name
        company_id = None
        if company_name:
            company = session.exec(select(Company).where(func.lower(Company.name) == company_name.lower())).first()
            if not company:
                company = Company(name=company_name, created_at=now_utc(), updated_at=now_utc())
                session.add(company)
                session.commit()
                session.refresh(company)
            company_id = company.id

        if not emails:
            emails = [""]

        for contact_email in emails:
            contact_first = first_name
            contact_last = last_name
            if (not contact_first and not contact_last) and contact_email:
                contact_first, contact_last = name_from_email(contact_email)
            if not contact_first and not contact_last:
                skipped += 1
                continue
            if contact_email:
                existing = session.exec(select(Contact).where(Contact.email == contact_email)).first()
                if existing:
                    skipped += 1
                    continue
            c = Contact(
                first_name=contact_first,
                last_name=contact_last,
                email=contact_email or None,
                phone=phone or None,
                role=role or None,
                company_id=company_id,
                notes=notes or None,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            session.add(c)
            session.commit()
            session.refresh(c)
            add_activity(session, "CREATE", "Contact", c.id, f"Imported contact: {c.first_name} {c.last_name}")
            imported += 1

    return RedirectResponse(url=f"/contacts?imported={imported}&skipped={skipped}", status_code=303)

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
    company_id: Optional[str] = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    c = session.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "Contact not found")
    before = {"first_name": c.first_name, "last_name": c.last_name, "email": c.email, "phone": c.phone, "role": c.role, "company_id": c.company_id, "notes": c.notes}
    company_id_value = parse_optional_int(company_id)
    c.first_name = first_name.strip()
    c.last_name = last_name.strip()
    c.email = (email.strip() or None)
    c.phone = (phone.strip() or None)
    c.role = (role.strip() or None)
    c.company_id = company_id_value
    c.notes = (notes.strip() or None)
    c.updated_at = now_utc()
    session.add(c)
    session.commit()
    after = {"first_name": c.first_name, "last_name": c.last_name, "email": c.email, "phone": c.phone, "role": c.role, "company_id": c.company_id, "notes": c.notes}
    changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changes:
        add_activity(session, "UPDATE", "Contact", c.id, f"Updated contact: {c.first_name} {c.last_name}", changes=changes)
    return RedirectResponse(url=f"/contacts/{c.id}", status_code=303)

@app.post("/contacts/{contact_id}/delete")
def contacts_delete(contact_id: int, next_url: str = Form("/contacts"), session=Depends(session_dep)):
    contact = session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    full_name = f"{contact.first_name} {contact.last_name}"
    leads = session.exec(select(Lead).where(Lead.contact_id == contact_id)).all()
    for lead in leads:
        lead.contact_id = None
        lead.updated_at = now_utc()
        session.add(lead)
    projects = session.exec(select(Project).where(Project.contact_id == contact_id)).all()
    for project in projects:
        project.contact_id = None
        project.updated_at = now_utc()
        session.add(project)
    assets = session.exec(select(Asset).where(Asset.contact_id == contact_id)).all()
    for asset in assets:
        asset.contact_id = None
        session.add(asset)
    events = session.exec(select(Event).where(Event.contact_id == contact_id)).all()
    for event in events:
        event.contact_id = None
        event.updated_at = now_utc()
        session.add(event)
    session.delete(contact)
    session.commit()
    add_activity(session, "DELETE", "Contact", contact_id, f"Deleted contact: {full_name}")
    return RedirectResponse(url=next_url, status_code=303)

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
    company_id: Optional[str] = Form(""),
    contact_id: Optional[str] = Form(""),
    next_step: str = Form(""),
    due_date: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    if status not in LEAD_STATUSES:
        status = "NEW"
    ve = parse_optional_float(value_estimate)
    dd = parse_optional_datetime(due_date)
    company_id_value = parse_optional_int(company_id)
    contact_id_value = parse_optional_int(contact_id)
    lead = Lead(
        title=title.strip(),
        status=status,
        source=(source.strip() or None),
        value_estimate=ve,
        company_id=company_id_value,
        contact_id=contact_id_value,
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
    company_id: Optional[str] = Form(""),
    contact_id: Optional[str] = Form(""),
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
    company_id_value = parse_optional_int(company_id)
    contact_id_value = parse_optional_int(contact_id)
    p = Project(
        name=name.strip(),
        status=status,
        company_id=company_id_value,
        contact_id=contact_id_value,
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
    project_id: Optional[str] = Form(""),
    contact_id: Optional[str] = Form(""),
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
    project_id_value = parse_optional_int(project_id)
    contact_id_value = parse_optional_int(contact_id)
    e = Event(
        title=title.strip(),
        start=start_dt,
        end=end_dt,
        all_day=bool(all_day),
        project_id=project_id_value,
        contact_id=contact_id_value,
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
def assets_list(
    request: Request,
    q: str = "",
    project_id: Optional[str] = "",
    contact_id: Optional[str] = "",
    file_type: str = "",
    view: str = "thumbs",
    session=Depends(session_dep),
):
    stmt = select(Asset)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Asset.filename.like(like), Asset.tags.like(like), Asset.notes.like(like)))
    project_id_value = parse_optional_int(project_id)
    if project_id_value:
        stmt = stmt.where(Asset.project_id == project_id_value)
    contact_id_value = parse_optional_int(contact_id)
    if contact_id_value:
        stmt = stmt.where(Asset.contact_id == contact_id_value)
    if file_type == "image":
        stmt = stmt.where(Asset.mime_type.like("image/%"))
    elif file_type == "video":
        stmt = stmt.where(Asset.mime_type.like("video/%"))
    elif file_type == "document":
        stmt = stmt.where(Asset.mime_type.like("application/%"))
    elif file_type == "other":
        stmt = stmt.where(
            or_(
                Asset.mime_type.is_(None),
                and_(
                    ~Asset.mime_type.like("image/%"),
                    ~Asset.mime_type.like("video/%"),
                    ~Asset.mime_type.like("application/%"),
                ),
            )
        )
    assets = session.exec(stmt.order_by(Asset.created_at.desc()).limit(200)).all()
    projects = session.exec(select(Project).order_by(Project.name)).all()
    contacts = session.exec(select(Contact).order_by(Contact.last_name, Contact.first_name)).all()
    view_value = view if view in {"thumbs", "list"} else "thumbs"
    return templates.TemplateResponse(
        "assets.html",
        {
            "request": request,
            "assets": assets,
            "projects": projects,
            "contacts": contacts,
            "q": q,
            "project_id": project_id_value or "",
            "contact_id": contact_id_value or "",
            "file_type": file_type,
            "view": view_value,
            "current_url": str(request.url),
        },
    )

async def save_asset_upload(
    file: UploadFile,
    *,
    tags: str,
    project_id_value: Optional[int],
    contact_id_value: Optional[int],
    notes: str,
    session,
) -> Optional[Asset]:
    if not file.filename:
        return None
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
    size_bytes = len(content)
    existing = session.exec(
        select(Asset).where(
            Asset.filename == safe_name,
            Asset.size_bytes == size_bytes,
            Asset.mime_type == mime,
        )
    ).first()
    if existing:
        return None

    stored_path.write_bytes(content)

    a = Asset(
        filename=safe_name,
        stored_path=str(stored_name),
        mime_type=mime,
        size_bytes=size_bytes,
        tags=(tags.strip() or None),
        project_id=project_id_value,
        contact_id=contact_id_value,
        notes=(notes.strip() or None),
        created_at=now_utc(),
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    add_activity(session, "UPLOAD", "Asset", a.id, f"Uploaded asset: {a.filename}", changes={"size_bytes": a.size_bytes, "mime_type": a.mime_type})
    return a

@app.post("/assets/upload")
async def assets_upload(
    files: list[UploadFile] = File(...),
    tags: str = Form(""),
    project_id: Optional[str] = Form(""),
    contact_id: Optional[str] = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    project_id_value = parse_optional_int(project_id)
    contact_id_value = parse_optional_int(contact_id)
    uploads = [
        await save_asset_upload(
            file,
            tags=tags,
            project_id_value=project_id_value,
            contact_id_value=contact_id_value,
            notes=notes,
            session=session,
        )
        for file in files
    ]
    if not any(uploads):
        return RedirectResponse(url="/assets?duplicate=1", status_code=303)
    return RedirectResponse(url="/assets", status_code=303)

@app.post("/projects/{project_id}/assets/upload")
async def project_assets_upload(
    project_id: int,
    files: list[UploadFile] = File(...),
    tags: str = Form(""),
    notes: str = Form(""),
    session=Depends(session_dep),
):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    uploads = [
        await save_asset_upload(
            file,
            tags=tags,
            project_id_value=project_id,
            contact_id_value=None,
            notes=notes,
            session=session,
        )
        for file in files
    ]
    if not any(uploads):
        return RedirectResponse(url=f"/projects/{project_id}?duplicate=1", status_code=303)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

@app.post("/assets/{asset_id}/delete")
def assets_delete(asset_id: int, next_url: str = Form("/assets"), session=Depends(session_dep)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    stored_path = UPLOAD_DIR / asset.stored_path
    if stored_path.exists():
        stored_path.unlink()
    session.delete(asset)
    session.commit()
    add_activity(session, "DELETE", "Asset", asset_id, f"Deleted asset: {asset.filename}")
    return RedirectResponse(url=next_url or "/assets", status_code=303)

# ---------- Activity ----------
@app.get("/activity", response_class=HTMLResponse)
def activity_feed(request: Request, session=Depends(session_dep)):
    items = session.exec(select(Activity).order_by(Activity.ts.desc()).limit(300)).all()
    return templates.TemplateResponse("activity.html", {"request": request, "items": items})
