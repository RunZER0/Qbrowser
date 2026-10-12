"""Stable execution contract and explicitly allowlisted executor discovery."""

from __future__ import annotations

import os
import re
from importlib.metadata import entry_points
from typing import Protocol

from qbrowser.store import JobStore


class Executor(Protocol):
    """An executor acts on one claimed task and persists its terminal/review state."""

    def execute(self, job: dict, store: JobStore) -> dict: ...


class NativeExecutor:
    name = "native"

    def execute(self, job: dict, store: JobStore) -> dict:
        from qbrowser.native import run_native
        return run_native(job, store)


class AgentSExecutor:
    name = "agent_s"

    def execute(self, job: dict, store: JobStore) -> dict:
        from qbrowser.runner import execute as execute_agent_s
        return execute_agent_s(job, store)


def executor_for(name: str) -> Executor:
    if name == "native":
        return NativeExecutor()
    if name == "agent_s":
        return AgentSExecutor()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", name):
        raise ValueError("Invalid executor name")
    enabled = os.getenv("QBROWSER_ENABLE_EXECUTOR_PLUGINS", "0").lower() in {"1", "true", "yes", "on"}
    allowlist = {value.strip() for value in os.getenv("QBROWSER_EXECUTOR_ALLOWLIST", "").split(",")
                 if value.strip()}
    if not enabled or name not in allowlist:
        raise ValueError(f"Executor {name!r} is not installed and explicitly allowlisted")
    matches = [point for point in entry_points(group="qbrowser.executors") if point.name == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one installed entry point for executor {name!r}")
    factory = matches[0].load()
    instance = factory() if isinstance(factory, type) else factory
    if not callable(getattr(instance, "execute", None)):
        raise TypeError("Executor plugin must provide execute(job, store)")
    return instance
