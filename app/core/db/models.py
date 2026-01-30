from typing import Any, Dict, List, Optional

from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column, CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB


class Unit(SQLModel, table = True):
    __tablename__ = "Units"
    __table_args__ = (
        CheckConstraint("char_length(trim(destination_username)) > 0", name="ck_messages_destination_username_nonempty"),
        CheckConstraint("char_length(trim(subject_text)) > 0", name="ck_messages_subject_text_nonempty"),
    )