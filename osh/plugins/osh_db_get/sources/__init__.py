"""Backup source implementations bundled with the osh_db_get plugin."""

from .db import DbSource
from .https import HttpSource, HttpsSource
from .odoosh import OdooshSource
from .ssh import SshSource

__all__ = ["DbSource", "HttpSource", "HttpsSource", "OdooshSource", "SshSource"]
