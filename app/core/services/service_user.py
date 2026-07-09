"""UserService — create and fetch users.

Takes a `Session` (session injection) so the API layer and tests control the
transaction. Raises `NotFoundError` for not-found and `ConflictError` for a taken
username/email, per the service conventions in SPEC.md.
"""

from uuid import UUID

from sqlmodel import Session, select

from app.core.db.models import User
from app.core.services.errors import ConflictError, NotFoundError


class UserService:
    def __init__(self, session: Session):
        self.session = session

    def create_user(self, username: str, email: str, password_hash: str) -> User:
        # Check for a taken username/email up front so we can raise a clear
        # ConflictError instead of surfacing a DB IntegrityError.
        clash = self.session.exec(
            select(User).where(
                (User.username == username) | (User.email == email)
            )
        ).first()
        if clash is not None:
            if clash.username == username:
                raise ConflictError(f"username {username!r} is already taken")
            raise ConflictError(f"email {email!r} is already taken")

        user = User(username=username, email=email, password_hash=password_hash)
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def get_user(self, user_id: UUID) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} not found")
        return user

    def set_admin(self, user_id: UUID, is_admin: bool) -> User:
        """Grant or revoke admin on a user; `NotFoundError` if they don't exist."""
        user = self.get_user(user_id)
        user.is_admin = is_admin
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user
