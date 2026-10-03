"""PostgreSQL persistence adapter."""

from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.models import Base
from svoi_pravila.adapters.persistence.probe import DatabaseProbe

__all__ = [
    "Base",
    "DatabaseProbe",
    "create_engine",
    "dispose_engine",
]
