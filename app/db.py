from __future__ import annotations

from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "crm.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})

def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)

def get_session() -> Session:
    return Session(engine)
