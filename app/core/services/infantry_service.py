from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select

from app.core.db.connection import get_session
from app.core.db.models import Infantry

