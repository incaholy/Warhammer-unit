from collections.abc import Sequence as SequenceABC
from typing import  Dict, Optional

from sqlmodel import select

from app.core.db.connection import get_session
from app.core.db.models import Weapon
#from app.core.api.units import get_unit

class WeaponService():
    def __init__(self):
        self.session = get_session()

    
    def create_weapon(
            self,
            name:str,
            unit_id,
    )-> Weapon:
        pass
        