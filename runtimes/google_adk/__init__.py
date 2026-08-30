"""Google ADK runtime adapter.

The adapter is optional so the runtime-neutral framework and its deterministic
CI path do not require Google credentials or the Google ADK dependency.
"""

from runtimes.google_adk.runtime import (
    GoogleAdkError,
    GoogleAdkRuntime,
    GoogleAdkRuntimeConfig,
)

__all__ = ["GoogleAdkError", "GoogleAdkRuntime", "GoogleAdkRuntimeConfig"]
