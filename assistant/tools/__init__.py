"""Tools the assistant can call to control the Mac."""

from . import mac  # noqa: F401  (import registers every tool)
from .registry import REGISTRY, dispatch, schemas  # noqa: F401
