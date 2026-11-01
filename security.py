"""Compatibility alias: the module moved to infra/security.py (older updaters still import it by its old name)."""
import sys

from infra import security as _moved

sys.modules[__name__] = _moved
