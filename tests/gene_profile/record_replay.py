# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What the renderer reads from the records, checked against what the records hold.

A table can only be as faithful as the keys it reads. The label-warnings table read
``warnings_and_cautions``, a key no connector writes, so its warnings column was empty in every
profile; its unit test passed because the hand-written fixture used the same wrong key, and a
static scan of the scripts misses it because the key was read through a variable. Watching the
reads at runtime, on real records, catches that class whatever the code looks like.

``replay`` renders every block of each recorded run with each record wrapped so that every key
the renderer looks up is logged by its path (``drugs[].name``). A path that no record of that
source holds, in any run, is a read the producers never satisfy. The same pass lists blocks that
fell back to their empty state although their main record answered ``ok``.

The key paths seen are the *shape registry*: values-free, so it can be committed. Unit tests
build record fixtures through ``record_fixture``, which rejects keys outside the registry and logs
reads outside it, so a fixture cannot repeat a renderer's wrong key.

Run on recorded profiles (no network, no LLM)::

    uv run python tests/gene_profile/record_replay.py results/profiles/*_2026-11-02 \\
        [--write-registry tests/fixtures/record_shapes.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".claude" / "skills" / "gene-profile" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

REGISTRY = _ROOT / "tests" / "fixtures" / "record_shapes.json"
WILDCARD = "*"
_LOWER = re.compile(r"[a-z]")
_DATA_KEYED_MIN = 60


def _id_like(key: str) -> bool:
    """A key that is a value, not a field name: an identifier, a drug name, a year, a locus."""
    return not _LOWER.search(key) or key[:1].isdigit()


def data_keyed(d: dict[str, Any]) -> bool:
    """A dict keyed by values (PMIDs, drug names, variant ids), whose keys vary by gene."""
    keys = [str(k) for k in d]
    return len(keys) > _DATA_KEYED_MIN or (bool(keys) and sum(map(_id_like, keys)) * 2 >= len(keys))


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")
_SEGMENT = re.compile(r"\[\]|[^.\[\]]+")
_RECURRENCE = 0.8  # share of the runs holding a parent that a field name appears in
_MIN_RUNS = 3


def _segments(path: str) -> list[str]:
    return _SEGMENT.findall(path)


def _join(segments: list[str]) -> str:
    return "".join(s if s == "[]" else f".{s}" for s in segments).lstrip(".")


def fold(seen: dict[str, dict[str, set[str]]]) -> dict[str, set[str]]:
    """The registry: key paths, with value-like keys folded into ``*``.

    A snake_case key is a field name wherever it appears. Any other key (a cell type, a
    protein domain, a study id) is a field name only if it recurs: it must appear in at least
    80% of the runs whose records hold its parent, over at least three runs. Otherwise it is a
    value that varies by gene and is folded, so the registry holds structure, not data.
    """
    out: dict[str, set[str]] = {}
    for source, paths in seen.items():
        runs_at = {p: r for p, r in paths.items()}
        folded: set[str] = set()
        for path, runs in paths.items():
            segs = _segments(path)
            norm: list[str] = []
            for i, seg in enumerate(segs):
                if seg in ("[]", WILDCARD) or _FIELD_NAME.fullmatch(seg):
                    norm.append(seg)
                    continue
                parent = _join(segs[:i])
                parent_runs = runs_at.get(parent, runs) if parent else _all_runs(paths)
                here = runs_at.get(_join(segs[: i + 1]), runs)
                recurs = len(parent_runs) >= _MIN_RUNS and len(here) >= _RECURRENCE * len(
                    parent_runs
                )
                norm.append(seg if recurs else WILDCARD)
            folded.add(_join(norm))
        out[source] = folded
    return out


def _all_runs(paths: dict[str, set[str]]) -> set[str]:
    return set().union(*paths.values()) if paths else set()


def satisfied(path: str, patterns: set[str]) -> bool:
    """Whether a key path matches a registry pattern, ``*`` standing for one key."""
    if path in patterns:
        return True
    segs = _segments(path)
    keys = [i for i, s in enumerate(segs) if s != "[]"]
    for mask in range(1, 1 << len(keys)):
        trial = list(segs)
        for bit, i in enumerate(keys):
            if mask >> bit & 1:
                trial[i] = WILDCARD
        if _join(trial) in patterns:
            return True
    return False


@dataclass
class Tracker:
    """Key paths each source holds, and the paths the renderer read."""

    seen: dict[str, dict[str, set[str]]] = field(default_factory=dict)  # source → path → runs
    reads: dict[str, dict[str, str]] = field(default_factory=dict)  # source → path → where
    readers: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (source, path) → runs
    by_block: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)
    loads: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)  # status
    run: str = ""
    block: str = ""

    def hold(self, source: str, path: str) -> None:
        self.seen.setdefault(source, {}).setdefault(path, set()).add(self.run)

    def read(self, source: str, path: str) -> None:
        at = self.reads.setdefault(source, {})
        if path not in at:
            at[path] = _caller()
        self.readers.setdefault((source, path), set()).add(self.run)
        if self.block:
            self.by_block.setdefault((self.run, self.block), set()).add((source, path))

    def unsatisfied(self, registry: dict[str, set[str]] | None = None) -> dict[str, dict[str, str]]:
        """Paths read that no record of the source holds (in this replay, or the registry)."""
        known = registry if registry is not None else fold(self.seen)
        out: dict[str, dict[str, str]] = {}
        for source, paths in self.reads.items():
            held = known.get(source, set())
            missing = {p: w for p, w in paths.items() if not satisfied(p, held)}
            if missing:
                out[source] = missing
        return out


def _caller() -> str:
    """The first frame in the skill's scripts: where the read happened."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_code.co_filename
        if name.startswith(str(_SCRIPTS)):
            return f"{Path(name).name}:{frame.f_lineno}"
        frame = frame.f_back
    return "?"


class TrackedDict(dict):  # type: ignore[type-arg]
    """A record dict that logs each key looked up, by path, against its source."""

    def __init__(
        self,
        data: Any = (),
        source: str = "",
        path: str = "",
        tracker: Tracker | None = None,
        wildcard: bool = False,
    ) -> None:
        super().__init__(data)
        self._source, self._path, self._tracker, self._wildcard = source, path, tracker, wildcard

    def _log(self, key: Any) -> None:
        if self._tracker is not None and not self._wildcard and isinstance(key, str):
            self._tracker.read(self._source, _child(self._path, key))

    def get(self, key: Any, default: Any = None) -> Any:
        self._log(key)
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        self._log(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self._log(key)
        return super().__contains__(key)

    def __reduce__(self) -> Any:  # copy/pickle as a plain dict
        return (dict, (dict(self),))


def wrap(value: Any, source: str, path: str, tracker: Tracker) -> Any:
    """Wrap a record's dicts (recursively) and register the paths it holds."""
    if isinstance(value, dict):
        wild = data_keyed(value)
        out = {}
        for k, v in value.items():
            sub = _child(path, WILDCARD if wild else str(k))
            tracker.hold(source, sub)
            out[k] = wrap(v, source, sub, tracker)
        return TrackedDict(out, source, path, tracker, wild)
    if isinstance(value, list):
        return [wrap(v, source, f"{path}[]", tracker) for v in value]
    return value


def shapes(value: Any, path: str = "") -> Iterator[str]:
    """The key paths a record holds, with the same wildcard rule as ``wrap``."""
    if isinstance(value, dict):
        wild = data_keyed(value)
        for k, v in value.items():
            sub = _child(path, WILDCARD if wild else str(k))
            yield sub
            yield from shapes(v, sub)
    elif isinstance(value, list):
        for v in value:
            yield from shapes(v, f"{path}[]")


@contextmanager
def tracking(tracker: Tracker) -> Iterator[Tracker]:
    """Every record ``load_raw`` returns is wrapped while the context is open."""
    import profile_schema

    model = profile_schema.RawRecord
    original = model.model_validate_json
    own = "model_validate_json" in model.__dict__

    def validated(cls: Any, raw: Any, *args: Any, **kwargs: Any) -> Any:
        rec = original(raw, *args, **kwargs)
        rec.data = wrap(rec.data, rec.key, "", tracker)
        if tracker.block:
            tracker.loads.setdefault((tracker.run, tracker.block), set()).add(
                (rec.key, str(rec.status))
            )
        return rec

    model.model_validate_json = classmethod(validated)  # type: ignore[method-assign,assignment]
    try:
        yield tracker
    finally:
        if own:
            model.model_validate_json = original  # type: ignore[method-assign]
        else:
            del model.model_validate_json


@dataclass
class Replay:
    tracker: Tracker
    empty_over_data: list[tuple[str, str, str]]  # (run, block, a read path holding data)
    failures: list[tuple[str, str]]  # (run, error)


def _has_data(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_data(v) for v in value.values())
    if isinstance(value, list):
        return bool(value)
    return value not in (None, "", 0, False)


def values_at(data: Any, path: str) -> list[Any]:
    """The values a key path reaches in a plain record (``[]`` and ``*`` fan out)."""
    nodes = [data]
    for part in re.findall(r"[^.\[\]]+|\[\]", path):
        nxt: list[Any] = []
        for node in nodes:
            if part == "[]" and isinstance(node, list):
                nxt.extend(node)
            elif part == WILDCARD and isinstance(node, dict):
                nxt.extend(node.values())
            elif isinstance(node, dict) and part in node:
                nxt.append(node[part])
        nodes = nxt
    return nodes


def replay(run_dirs: Iterable[Path]) -> Replay:
    """Render every block of every run with tracked records."""
    import profile_blocks

    tracker = Tracker()
    empties: list[tuple[str, str, str]] = []
    failures: list[tuple[str, str]] = []
    tables = dict(profile_blocks._TABLES)

    def attributed(name: str, build: Any) -> Any:
        def run(ctx: Any) -> Any:
            tracker.block = name
            try:
                return build(ctx)
            finally:
                tracker.block = ""

        return run

    profile_blocks._TABLES.update({n: attributed(n, f) for n, f in tables.items()})
    try:
        _replay_runs(run_dirs, tracker, empties, failures)
    finally:
        profile_blocks._TABLES.update(tables)
    return Replay(tracker, empties, failures)


def _replay_runs(
    run_dirs: Iterable[Path],
    tracker: Tracker,
    empties: list[tuple[str, str, str]],
    failures: list[tuple[str, str]],
) -> None:
    import profile_blocks
    from profile_schema import load_manifest, load_raw, load_sources

    draft = "\n\n".join(f"{{{{{name}}}}}" for name in profile_blocks.BLOCKS)
    with tracking(tracker):
        for run_dir in run_dirs:
            if not (run_dir / "manifest.json").exists():
                continue
            tracker.run = run_dir.name
            try:
                m = load_manifest(run_dir)
                _text, notes = profile_blocks.expand(
                    draft, run_dir, m.gene, set(load_sources(run_dir)),
                    figures=False, disease=m.disease,
                )  # fmt: skip
            except Exception as exc:  # noqa: BLE001 — a replay reports, it does not stop
                failures.append((run_dir.name, f"{type(exc).__name__}: {exc}"))
                continue
            for note in notes:
                name, _sep, what = note.partition(": ")
                spec = profile_blocks.BLOCKS.get(name)
                if what != "empty state" or spec is None or spec.source == "llm":
                    continue
                # A block may need several inputs, so its empty state is suspect only when every
                # path it read holds data; one empty input is reason enough to show nothing.
                loaded = tracker.loads.get((run_dir.name, name), set())
                if any(status != "ok" for _key, status in loaded):
                    continue  # an input answered empty or was not checked
                read = sorted(tracker.by_block.get((run_dir.name, name), set()))
                held = []
                for source, path in read:
                    rec = load_raw(run_dir, source)
                    plain = json.loads(json.dumps(rec.data)) if rec is not None else None
                    held.append(any(_has_data(v) for v in values_at(plain, path)))
                if read and all(held):
                    where = "; ".join(f"{s}: {p}" for s, p in read[:4])
                    empties.append((run_dir.name, name, where))


def load_registry(path: Path = REGISTRY) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    return {k: set(v) for k, v in json.loads(path.read_text()).items()}


def write_registry(
    seen: dict[str, dict[str, set[str]]], path: Path = REGISTRY, *, merge: bool = False
) -> None:
    """Write the registry from this replay. It replaces the file unless ``merge``: a union
    would keep a key a connector no longer writes, the very read this check exists to catch."""
    merged = load_registry(path) if merge else {}
    for source, paths in fold(seen).items():
        merged.setdefault(source, set()).update(paths)
    path.write_text(json.dumps({k: sorted(v) for k, v in sorted(merged.items())}, indent=1) + "\n")


def record_fixture(source: str, data: Any, tracker: Tracker | None = None) -> Any:
    """A unit-test record, checked against the shape registry.

    Raises if the fixture holds a key path no recorded run of ``source`` holds, so a fixture
    cannot invent a key; reads are logged on ``tracker`` for ``assert_reads_known``.
    """
    known = load_registry().get(source)
    if known is None:
        raise KeyError(f"{source} is not in {REGISTRY.name}: replay a recorded run first")
    unknown = sorted(p for p in set(shapes(data)) if not satisfied(p, known))
    if unknown:
        raise ValueError(f"{source} fixture holds keys no recorded run holds: {unknown}")
    return wrap(data, source, "", tracker or Tracker())


def assert_reads_known(tracker: Tracker) -> None:
    """Fail on any key the code read that no recorded run of that source holds."""
    missing = tracker.unsatisfied(load_registry())
    assert not missing, "reads of keys no recorded run holds:\n" + "\n".join(
        f"  {s}: {p} (read at {w})" for s, paths in sorted(missing.items())
        for p, w in sorted(paths.items())
    )  # fmt: skip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("run_dirs", nargs="+", type=Path)
    p.add_argument("--write-registry", type=Path, default=None)
    p.add_argument(
        "--merge", action="store_true", help="add to the registry instead of replacing it"
    )
    args = p.parse_args()
    result = replay(args.run_dirs)
    t = result.tracker
    missing = t.unsatisfied()
    print(f"runs: {len({r for rs in t.readers.values() for r in rs})} · "
          f"sources read: {len(t.reads)} · paths read: {sum(map(len, t.reads.values()))}")  # fmt: skip
    print(f"\nreads no record of the source holds ({sum(map(len, missing.values()))}):")
    for source, paths in sorted(missing.items()):
        for path, where in sorted(paths.items()):
            print(f"  {source}: {path}  (read at {where})")
    print(f"\nempty state although a path it read holds data ({len(result.empty_over_data)}):")
    for run, block, where in result.empty_over_data:
        print(f"  {run}: {block} ({where})")
    for run, err in result.failures:
        print(f"  FAILED {run}: {err}")
    if args.write_registry:
        write_registry(t.seen, args.write_registry, merge=args.merge)
        print(f"\nregistry: {args.write_registry}")


if __name__ == "__main__":
    main()
