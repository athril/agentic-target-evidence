# Publishing the MCP gateway

> Part of the [docs/](README.md) documentation set. How the `mcp-gateway` image gets listed in
> the official MCP Registry so people can install it without cloning the repo. For what the
> gateway *is*, see [mcp_gateway.md](mcp_gateway.md).

The gateway is published once, as an OCI image on GHCR, and listed in the
[official MCP Registry](https://registry.modelcontextprotocol.io) as
**`io.github.athril/agentic-target-evidence`**. Directories such as Glama and PulseMCP mirror
that registry, so one listing reaches all of them. Clients install it as
`docker run -i --rm -e MCP_TRANSPORT=stdio ghcr.io/athril/agentic-target-evidence/mcp-gateway:<tag>`.

---

## The three pieces

| Piece | Role |
|---|---|
| [server.json](../server.json) | The registry entry: name, description, the image to run, its stdio transport, and the env vars a client should ask for (`NCBI_API_KEY`, `USPTO_API_KEY`, both optional). The committed `version` and image tag are placeholders; CI overwrites them on each release. |
| `LABEL io.modelcontextprotocol.server.name` in the `mcp-gateway` stage of the [Dockerfile](../Dockerfile) | Proves the image belongs to that entry. The registry pulls the image manifest and rejects the publish if this label doesn't match `name` in `server.json`. |
| `publish-mcp-registry` job in [ci.yml](../.github/workflows/ci.yml) | Runs after `publish-ghcr` on every release: stamps the version into `server.json`, validates it, logs in with GitHub OIDC (no secret), and publishes. |

Renaming the entry means changing **both** `name` in `server.json` and the Dockerfile label.
Names under `io.github.athril/` are authorized by GitHub login; any other prefix needs DNS
verification of a domain you own.

---

## Normal release flow

Nothing to do by hand. Merging the release-please PR tags `vX.Y.Z`, then:

1. `publish-ghcr` builds and pushes all seven images as `:vX.Y.Z` and `:latest`.
2. `publish-mcp-registry` publishes `server.json` with `version: X.Y.Z` and image
   `mcp-gateway:vX.Y.Z`.

Check the result:

```bash
curl -s "https://registry.modelcontextprotocol.io/v0.1/servers?search=agentic-target-evidence" | jq
```

---

## Package visibility

The registry can only verify a public image. Images pushed by `publish-ghcr` (with the
workflow's `GITHUB_TOKEN`) are linked to this repo and inherit its visibility, so in a public
repo they are public from the first push, with no manual step. Packages are listed at
`https://github.com/athril?tab=packages` and in the repo sidebar, not on the profile overview.

If a package was ever pushed by hand (e.g. `docker push` with a personal token), it may be
private and unlinked. Fix it under **Package settings → Change visibility → Public**, then
re-run the failed `publish-mcp-registry` job.

To confirm an image is public without logging out of your own Docker session, pull it with an
empty Docker config:

```bash
DOCKER_CONFIG=$(mktemp -d) docker pull ghcr.io/athril/agentic-target-evidence/mcp-gateway:latest
```

---

## Publishing by hand

Use this if CI is unavailable, or to fix a listing between releases. The image for that tag must
already be on GHCR, public, and built with the label.

```bash
# Install the publisher CLI (pin the same version CI uses)
curl -fsSL https://github.com/modelcontextprotocol/registry/releases/download/v1.8.1/mcp-publisher_linux_amd64.tar.gz \
  | tar xz mcp-publisher

# Point server.json at the release you're publishing
TAG=v0.1.3
jq --arg v "${TAG#v}" --arg tag "$TAG" \
  '.version = $v | .packages[0].identifier |= sub(":[^:]+$"; ":" + $tag)' \
  server.json > server.tmp && mv server.tmp server.json

./mcp-publisher validate
./mcp-publisher login github      # opens a browser device-code flow
./mcp-publisher publish
git checkout server.json          # don't commit the stamped copy
```

Each `version` can be published only once. To fix a bad listing, publish a new version. To pull
one, mark it deprecated:

```bash
./mcp-publisher status --status deprecated --message "use 0.1.4" io.github.athril/agentic-target-evidence 0.1.3
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Publish fails on image verification / manifest fetch | The tag doesn't exist on GHCR (check the `publish-ghcr` job), or the package is private (see "Package visibility"). |
| Publish fails with a label/name mismatch | The image was built before the label was added, or `name` and the label differ. Images from releases up to and including `v0.1.2` have no label. |
| `validate` fails on `description` | It has a 100-character limit. |
| `docker pull …:latest` fails but `:vX.Y.Z` works | That image was only pushed by a manual backfill (`workflow_dispatch` with `publish_tag`), which deliberately skips `latest`. The next normal release fixes it. |
| The client starts the container but hangs | `-e MCP_TRANSPORT=stdio` is missing: the image defaults to HTTP. |

---

## Other directories (optional)

- **Docker MCP Catalog** (hub.docker.com/mcp): a reviewed catalog surfaced in Docker Desktop's MCP
  Toolkit. Submit by opening a PR to
  [docker/mcp-registry](https://github.com/docker/mcp-registry) that points at this repo and the
  `mcp-gateway` Dockerfile target. Review takes about 1–7 days.
- **Anthropic Connectors Directory**: lists only *hosted* remote servers. That needs a public
  HTTPS deployment, OAuth instead of the static `MCP_GATEWAY_TOKEN`, and a plan for the shared
  NCBI/USPTO key rate limits, so it is out of scope until the gateway is hosted somewhere.
