# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Check the network and proxy setup of the shell that starts a panel run.

A proxy variable in the console can break a host the pipeline calls. This prints which
proxy variables are set (names only, never values), then makes one https:// GET to each
external host the pipeline calls, through the same environment settings httpx uses.

Only https is checked: the pipeline makes no plain-http request (the OLS parents link it
was given over http:// is requested over https), so an http proxy that cannot resolve,
as the console's could, does not affect a run.

    uv run python scripts/preflight_network.py

Exits non-zero, naming the failing scheme and host, when any request fails.
"""

from __future__ import annotations

import os
import sys

import httpx

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")

# host, path: one cheap URL per external host the pipeline calls. The check is about the
# host and scheme, so one URL per host: www.ebi.ac.uk serves both OLS4 and Europe PMC
# (Europe PMC's own paths drop plain http server-side).
HOSTS = (
    ("www.ebi.ac.uk", "/ols4/api/ontologies/mondo"),  # OLS4, Europe PMC
    ("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/einfo.fcgi"),  # NCBI E-utilities
    ("api.platform.opentargets.org", "/api/v4/graphql/schema"),  # Open Targets
    ("clinicaltrials.gov", "/api/v2/version"),  # ClinicalTrials.gov
    ("api.fda.gov", "/drug/label.json?limit=1"),  # openFDA
    ("gnomad.broadinstitute.org", "/"),  # gnomAD
)


def _proxy_names() -> list[str]:
    names = [v.lower() for v in PROXY_VARIABLES] + list(PROXY_VARIABLES)
    return sorted(n for n in dict.fromkeys(names) if os.environ.get(n))


def _check(client: httpx.Client, url: str) -> str | None:
    """None when the host answers; otherwise a short reason."""
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"
    if resp.status_code >= 500:
        return f"HTTP {resp.status_code}"
    return None


def main() -> int:
    names = _proxy_names()
    print(f"proxy variables set: {', '.join(names) if names else 'none'}")
    failures: list[str] = []
    with httpx.Client(trust_env=True, timeout=10) as client:
        for host, path in HOSTS:
            reason = _check(client, f"https://{host}{path}")
            status = "ok" if reason is None else f"FAILED ({reason})"
            print(f"{host:32} {status}")
            if reason is not None:
                failures.append(f"https://{host}")
    if failures:
        print(f"\nnetwork preflight failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
