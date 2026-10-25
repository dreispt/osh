"""Plugin redeclaring the built-in 'host' runtime."""

from osh.runtimes import Runtime


class HostAgain(Runtime):
    name = "host"
    runtime_type = "runtime"
