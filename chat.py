"""Compatibility alias: the module moved to dash/chat.py (older updaters still import it by its old name)."""
import sys

from dash import chat as _moved

sys.modules[__name__] = _moved
