# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Degradations: where a run went on with less than it asked for.

Optional lookups degrade gracefully, so a run keeps going, but a WARNING line in a log was
the only trace. A cross-ontology disease lookup failed with a DNS error in every run of a
development panel, narrowing each run's disease scope, and was found only by grepping the
logs. Code anywhere now calls `record_degradation`. The workflow wraps every node with
`collecting`, so whatever was recorded while the node ran lands in the run's state
(``degradations``), the dossier header and the panel score.

The ledger is a context variable: a degradation recorded outside a node (a test, a script)
is only logged.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from schemas.state import merge_degradations

logger = logging.getLogger(__name__)

_ledger: ContextVar[list[dict[str, str]] | None] = ContextVar("degradations", default=None)


def record_degradation(component: str, cause: str, effect: str) -> None:
    """Log a degradation and, inside a node, add it to the run's ledger.

    ``component`` names what degraded ("disease scope: cross-ontology parents",
    "network: <host>"), ``cause`` why (the error), ``effect`` what the run lacks as a
    result, in words a reader of the dossier understands.
    """
    logger.warning("degraded: %s — %s (%s)", component, effect, cause)
    entries = _ledger.get()
    if entries is not None:
        entries.append({"component": component, "cause": cause[:300], "effect": effect})


def recorded() -> list[dict[str, str]]:
    """The degradations recorded so far by the running node (not yet in state)."""
    return list(_ledger.get() or [])


def _with_entries(node: str, result: Any, entries: list[dict[str, str]]) -> Any:
    if not isinstance(result, dict):
        return result
    # A source node that failed reports it in ``failed_sources``; it is a degradation too.
    failed = [
        {
            "component": f"source: {name}",
            "cause": "acquisition failed",
            "effect": "the run has no evidence from this source",
        }
        for name in result.get("failed_sources") or []
    ]
    found = [{**e, "node": node} for e in [*entries, *failed]]
    if not found:
        return result
    return {**result, "degradations": merge_degradations(result.get("degradations"), found)}


def collecting(node: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """``fn`` (a graph node) with the degradations recorded while it runs added to its
    state update. The wrapper keeps ``fn``'s signature, so the graph still passes it a
    config when it asks for one."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def run_async(*args: Any, **kwargs: Any) -> Any:
            entries: list[dict[str, str]] = []
            token = _ledger.set(entries)
            try:
                result = await fn(*args, **kwargs)
            finally:
                _ledger.reset(token)
            return _with_entries(node, result, entries)

        return run_async

    @functools.wraps(fn)
    def run_sync(*args: Any, **kwargs: Any) -> Any:
        entries: list[dict[str, str]] = []
        token = _ledger.set(entries)
        try:
            result = fn(*args, **kwargs)
        finally:
            _ledger.reset(token)
        return _with_entries(node, result, entries)

    return run_sync


def render_degradations(entries: list[dict[str, Any]] | None) -> str:
    """Dossier block listing a run's degradations; "" for a clean run."""
    if not entries:
        return ""
    lines = [
        f"- **{d.get('component', '?')}** — {d.get('effect', '')} "
        f"(`{d.get('cause', '')}`{', in ' + str(d['node']) if d.get('node') else ''})"
        for d in entries
    ]
    return (
        f"> ⚠ **Degraded run:** {len(entries)} lookup(s) failed or fell back; the "
        "conclusions below rest on less evidence than the run asked for.\n\n"
        + "\n".join(lines)
        + "\n"
    )
