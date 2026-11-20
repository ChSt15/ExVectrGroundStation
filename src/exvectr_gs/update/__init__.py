"""Self-update from git (docs/updating.md)."""

from .gitops import UpdateError
from .guards import update_guards
from .service import UpdateService

__all__ = ["UpdateError", "UpdateService", "update_guards"]
