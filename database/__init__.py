"""Database package for Law Office Bot v2."""

from .connection import close_connection, get_connection
from .schema import initialize_database

__all__ = ["close_connection", "get_connection", "initialize_database"]
