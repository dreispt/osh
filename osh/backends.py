"""Deprecated alias for :mod:`osh.runtimes`.

Kept so plugins written against the old backend API keep importing; new
code should import from ``osh.runtimes``. Scheduled for removal in a later
release.
"""

import sys

from . import runtimes

sys.modules[__name__] = runtimes
