"""Compatibility alias: use :mod:`runtimes.custom.runtime`."""

from runtimes.custom.runtime import CustomRuntime, CustomRuntimeConfig

AdkRuntime = CustomRuntime
AdkRuntimeConfig = CustomRuntimeConfig

__all__ = ["AdkRuntime", "AdkRuntimeConfig"]
