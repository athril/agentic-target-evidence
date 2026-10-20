# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-token estimates from the model's measured characters per token.

Prompts are packed close to the context window, and Ollama drops the oldest tokens
beyond it without an error. A fixed characters-per-token constant measured on one model
undercounts on another (3.2 measured on qwen2.5 left Qwen3.5 lens prompts 10% over
budget), so the ratio is learned: each local completion reports its prompt characters
and the prompt tokens the server counted.

The ratio depends on the prompt as much as on the model: screening prompts (abstract
prose) ran 3.6–4.0 characters per token on Qwen3.5 and lens prompts (ids, numbers, JSON)
about 3.1, so one pooled ratio undercounted lens prompts by 10–13% and filled the window.
Samples are therefore kept per (model, task), the last ``_WINDOW`` of each, persisted to
``TOKEN_RATIO_PATH`` (default ``.cache/token_ratio.json``). The estimate uses the task's
lower quartile (``_QUANTILE``), since undercounting is the costly error. A task with
fewer than ``_MIN_TASK_SAMPLES`` samples borrows the model's densest task (the lowest
ratio); before any measurement the ratio is ``DEFAULT_RATIO``.

A sample outside ``_PLAUSIBLE`` is ignored: when the server reuses a cached prompt
prefix it reports only the tokens it evaluated, which would make the ratio look several
times larger than it is.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RATIO = 3.0
_WINDOW = 50
_PLAUSIBLE = (1.5, 6.0)
# Prompts shorter than this say little about the ratio (the chat template dominates).
_MIN_SAMPLE_CHARS = 2000
_MIN_TASK_SAMPLES = 3
_QUANTILE = 25  # percentile of a task's ratios used for estimates (lower = more tokens)
_SEP = "|"


def _default_path() -> Path:
    return Path(os.environ.get("TOKEN_RATIO_PATH", ".cache/token_ratio.json"))


class TokenEstimator:
    """Characters per token by model and task, from measured prompts (see module
    docstring)."""

    def __init__(self, path: Path | None = None, default: float = DEFAULT_RATIO) -> None:
        self._path = path if path is not None else _default_path()
        self._default = default
        self._samples: dict[str, list[float]] = {}  # "model|task" → ratios, oldest first
        self._last_model: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            # Keys without a task are from the pooled format, which mixed prompt kinds.
            self._samples = {
                str(k): [float(r) for r in rs][-_WINDOW:]
                for k, rs in raw.items()
                if _SEP in str(k) and isinstance(rs, list) and rs
            }
            # The file keeps the last-measured key last (see `observe`).
            last = next(reversed(self._samples), None)
            self._last_model = last.split(_SEP, 1)[0] if last else None

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._samples))
        except OSError as exc:  # a read-only checkout still estimates, it just forgets
            logger.debug("token ratio not persisted: %s", exc)

    @staticmethod
    def _low(samples: list[float]) -> float:
        if len(samples) == 1:
            return samples[0]
        return statistics.quantiles(samples, n=100, method="inclusive")[_QUANTILE - 1]

    def ratio(self, model: str | None = None, task: str | None = None) -> float:
        """The task's lower-quartile ratio for ``model`` (the last measured model when None);
        with too few samples, the model's densest task; ``default`` before any measurement."""
        model = model if model is not None and self._has(model) else self._last_model
        if model is None:
            return self._default
        own = self._samples.get(f"{model}{_SEP}{task or ''}") or []
        if len(own) >= _MIN_TASK_SAMPLES:
            return self._low(own)
        tasks = [
            rs
            for k, rs in self._samples.items()
            if k.startswith(f"{model}{_SEP}") and len(rs) >= _MIN_TASK_SAMPLES
        ]
        return min((self._low(rs) for rs in tasks), default=self._default)

    def _has(self, model: str) -> bool:
        return any(k.startswith(f"{model}{_SEP}") for k in self._samples)

    def observe(self, model: str, prompt_chars: int, prompt_tokens: int, task: str = "") -> None:
        """Record one completion's prompt size in characters and in server-counted tokens."""
        if prompt_chars < _MIN_SAMPLE_CHARS or prompt_tokens <= 0:
            return
        ratio = prompt_chars / prompt_tokens
        if not _PLAUSIBLE[0] <= ratio <= _PLAUSIBLE[1]:
            return
        key = f"{model}{_SEP}{task}"
        samples = self._samples.pop(key, [])  # re-inserted last: the newest key
        samples.append(round(ratio, 3))
        self._samples[key] = samples[-_WINDOW:]
        self._last_model = model
        self._save()

    def estimate(self, text: str, model: str | None = None, task: str | None = None) -> int:
        """Tokens ``text`` will take for ``model`` in ``task`` (at least 1)."""
        return max(1, math.ceil(len(text) / self.ratio(model, task)))


_estimator: TokenEstimator | None = None


def get_estimator() -> TokenEstimator:
    global _estimator
    if _estimator is None:
        _estimator = TokenEstimator()
    return _estimator


def set_estimator(estimator: TokenEstimator) -> None:
    """Replace the process-wide estimator (tests start each case from a fresh one)."""
    global _estimator
    _estimator = estimator


def estimate_tokens(text: str, model: str | None = None, task: str | None = None) -> int:
    return get_estimator().estimate(text, model, task)
