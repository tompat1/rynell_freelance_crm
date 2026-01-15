from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Column
from sqlalchemy.types import JSON

class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    website: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    status: str = "NEW"  # NEW, CONTACTED, QUALIFIED, PROPOSAL, WON, LOST
    source: Optional[str] = None
    value_estimate: Optional[float] = None
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    next_step: Optional[str] = None
    due_date: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Idea(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    status: str = "BACKLOG"  # BACKLOG, IN_PROGRESS, PARKED, DONE
    tags: Optional[str] = None  # comma-separated for MVP
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    status: str = "ACTIVE"  # ACTIVE, ON_HOLD, DONE, ARCHIVED
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    title: str
    status: str = "TODO"  # TODO, DOING, BLOCKED, DONE
    due_date: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    location: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Asset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    stored_path: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    tags: Optional[str] = None
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Activity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    action: str  # CREATE, UPDATE, DELETE, NOTE, UPLOAD, STATUS
    entity_type: str  # Contact, Lead, Project, ...
    entity_id: Optional[int] = None
    summary: str
    changes: Optional[dict] = Field(default=None, sa_column=Column(JSON))
