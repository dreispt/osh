"""Deprecated alias for :mod:`osh.commands.runtime_cmd`.

Kept so plugins written against the old backend API keep importing; new
code should import from ``osh.commands.runtime_cmd``. Scheduled for
removal in a later release.
"""

import sys

from . import runtime_cmd

sys.modules[__name__] = runtime_cmd
