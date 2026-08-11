"""AuthService — registration and login.

Registration hashes the password and delegates to UserService (so the
duplicate-username/email check lives in one place). Login looks a user up by
username *or* email and verifies the password.
"""


from sqlmodel import Session, select

from app.core.db.models import User
from app.core.security import hash_password, verify_password
from app.core.services.service_user import UserService


class AuthService:
    def __init__(self, session: Session):
        self.session = session
        self._users = UserService(session)

    def register(self, username: str, email: str, password: str) -> User:
        return self._users.create_user(username, email, hash_password(password))

    def authenticate(self, identifier: str, password: str) -> User | None:
        """Return the user for a matching username/email + password, else None."""
        user = self.session.exec(
            select(User).where(
                (User.username == identifier) | (User.email == identifier)
            )
        ).first()
        if user is not None and verify_password(password, user.password_hash):
            return user
        return None
