"""
``harness: hermes`` wrap — Omnigent harness entry point for Hermes.

Thin module exposing :func:`create_app` — the entrypoint the shared
:mod:`omnigent.runtime.harnesses._runner` invokes after the parent
process resolves ``"hermes"`` to this module via
:data:`omnigent.runtime.harnesses._HARNESS_MODULES`.

Mirrors the pi wrap (``omnigent/inner/pi_harness.py``) and claude-sdk
wrap (``omnigent/inner/claude_sdk_harness.py``). Internally, instantiates
:class:`omnigent.runtime.harnesses._executor_adapter.ExecutorAdapter`
around a :class:`hermes_omnigent_harness.hermes_executor.HermesExecutor`
configured from env vars the parent process sets before spawning.

See the module docstring in ``hermes_executor.py`` for the full list of
``HARNESS_HERMES_*`` env vars.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter

from .hermes_executor import _build_hermes_executor

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build the Hermes harness's FastAPI app.

    Required entry point per the harness contract — the runner imports
    this module (resolved from
    :data:`omnigent.runtime.harnesses._HARNESS_MODULES`) and invokes
    ``create_app()`` to get the app it serves.

    :returns: The FastAPI app from :class:`ExecutorAdapter`'s
        :meth:`build` method, with all routes from the harness API
        subset wired up. The wrapped :class:`HermesExecutor` is
        constructed lazily on the first turn (so an absent Hermes
        install surfaces as a request-time error, not a FastAPI
        app-boot crash).
    """
    adapter = ExecutorAdapter(executor_factory=_build_hermes_executor)
    return adapter.build()
