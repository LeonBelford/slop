"""Compatibility alias: the module moved to infra/knowledge.py (older updaters still import it by its old name)."""
import sys

from infra import knowledge as _moved

sys.modules[__name__] = _moved
