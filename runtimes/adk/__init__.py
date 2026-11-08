"""Deprecated compatibility alias for :mod:`runtimes.custom`.

This package never integrated Google ADK (see ADR 0008). The reference loop now
lives in ``runtimes.custom`` as ``CustomRuntime``; ``AdkRuntime`` and
``AdkRuntimeConfig`` remain importable from here as aliases of the same classes.
Google ADK itself is ``runtimes.google_adk``.
"""

from runtimes.custom import CustomRuntime, CustomRuntimeConfig

AdkRuntime = CustomRuntime
AdkRuntimeConfig = CustomRuntimeConfig

__all__ = [
    "AdkRuntime",
    "AdkRuntimeConfig",
]
