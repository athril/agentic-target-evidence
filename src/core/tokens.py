# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-token estimates from the model's measured characters per token.

Prompts are packed close to the context window, and Ollama drops the oldest tokens
beyond it without an error. A fixed characters-per-token constant measured on one model
undercounts on another (3.2 measured on qwen2.5 left Qwen3.5 lens prompts 10% over
budget), so the ratio is learned: each local completion reports its prompt characters
and the prompt tokens the server counted, and the estimate uses the median of the
model's last ``_WINDOW`` ratios, persisted per model (``TOKEN_RATIO_PATH``, default
``.cache/token_ratio.json``). Before any measurement the ratio is ``DEFAULT_RATIO``, on
the conservative side for prompts dense in ids, numbers and JSON.

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


def _default_path() -> Path:
    return Path(os.environ.get("TOKEN_RATIO_PATH", ".cache/token_ratio.json"))


class TokenEstimator:
    """Characters per token by model, from measured prompts (see module docstring)."""

    def __init__(self, path: Path | None = None, default: float = DEFAULT_RATIO) -> None:
        self._path = path if path is not None else _default_path()
        self._default = default
        self._samples: dict[str, list[float]] = {}
        self._last_model: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._samples = {
                str(m): [float(r) for r in rs][-_WINDOW:]
                for m, rs in raw.items()
                if isinstance(rs, list) and rs
            }
            # The file keeps the last-measured model last (see `observe`).
            self._last_model = next(reversed(self._samples), None)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._samples))
        except OSError as exc:  # a read-only checkout still estimates, it just forgets
            logger.debug("token ratio not persisted: %s", exc)

    def ratio(self, model: str | None = None) -> float:
        """The model's median measured ratio; the last measured model's when ``model`` is
        None or unmeasured; ``default`` before any measurement."""
        samples = self._samples.get(model or "") or self._samples.get(self._last_model or "")
        return statistics.median(samples) if samples else self._default

    def observe(self, model: str, prompt_chars: int, prompt_tokens: int) -> None:
        """Record one completion's prompt size in characters and in server-counted tokens."""
        if prompt_chars < _MIN_SAMPLE_CHARS or prompt_tokens <= 0:
            return
        ratio = prompt_chars / prompt_tokens
        if not _PLAUSIBLE[0] <= ratio <= _PLAUSIBLE[1]:
            return
        samples = self._samples.pop(model, [])  # re-inserted last: the newest model
        samples.append(round(ratio, 3))
        self._samples[model] = samples[-_WINDOW:]
        self._last_model = model
        self._save()

    def estimate(self, text: str, model: str | None = None) -> int:
        """Tokens ``text`` will take for ``model`` (at least 1)."""
        return max(1, math.ceil(len(text) / self.ratio(model)))


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


def estimate_tokens(text: str, model: str | None = None) -> int:
    return get_estimator().estimate(text, model)
