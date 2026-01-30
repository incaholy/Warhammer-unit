from typing import Any, Dict, List, Optional

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column, CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB