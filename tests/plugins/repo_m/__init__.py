"""Plugin redeclaring the built-in 'none' backend."""

from osh.backends import Backend


class NoneAgain(Backend):
    name = "none"
    backend_type = "backend"
