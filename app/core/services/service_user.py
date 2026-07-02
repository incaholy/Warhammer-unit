"""UserService — create and fetch users.

Takes a `Session` (session injection) so the API layer and tests control the
transaction. Raises `LookupError` for not-found and `ValueError` for bad input,
per the service conventions in SPEC.md.
"""

from uuid import UUID

from sqlmodel import Session, select

from app.core.db.models import User


class UserService:
    def __init__(self, session: Session):
        self.session = session

    def create_user(self, username: str, email: str, password_hash: str) -> User:
        # Check for a taken username/email up front so we can raise a clear
        # ValueError instead of surfacing a DB IntegrityError.
        clash = self.session.exec(
            select(User).where(
                (User.username == username) | (User.email == email)
            )
        ).first()
        if clash is not None:
            if clash.username == username:
                raise ValueError(f"username {username!r} is already taken")
            raise ValueError(f"email {email!r} is already taken")

        user = User(username=username, email=email, password_hash=password_hash)
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def get_user(self, user_id: UUID) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise LookupError(f"user {user_id} not found")
        return user
