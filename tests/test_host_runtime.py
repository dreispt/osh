"""Tests for the built-in ``host`` runtime and its legacy ``none`` name."""

from osh.db import set_project_config


class TestHostRuntime:
    """The built-in ``host`` runtime and its legacy ``none`` name."""

    def test_none_backend_alias(self):
        """``NoneBackend`` remains importable and is the host runtime."""
        from osh.backends import HostBackend, NoneBackend

        assert NoneBackend is HostBackend
        assert HostBackend.name == "host"
        assert HostBackend.label == "Host"

    def test_get_backend_class_resolves_legacy_names(self):
        """``none`` (and ``local``) resolve to ``HostBackend``."""
        from osh.backends import HostBackend
        from osh.utils.plugin_loader import get_backend_class

        for name in ("host", "none", "local"):
            assert get_backend_class(name) is HostBackend

    def test_resolve_backend_with_legacy_run_target(self, tmp_project):
        """A project recorded with ``run.target = none`` runs on the host."""
        from osh.backends import HostBackend
        from osh.db import resolve_backend

        set_project_config(tmp_project, "run", "target", "none")

        assert isinstance(resolve_backend(tmp_project), HostBackend)
