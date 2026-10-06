# Governance

puenteo is MIT-licensed. Its founder and lead maintainer is [@mano7onam](https://github.com/mano7onam).

## Roles

| Role | Can | Becomes one by |
|---|---|---|
| **Contributor** | open issues and PRs, publish plugins | doing it |
| **Area maintainer** (person or organization) | review, approve and merge PRs in their area (`CODEOWNERS` path), triage its issues | about 3 good merged PRs in the area, or owning the integrated product (e.g. a vendor maintaining its own provider), plus a lead-maintainer nomination |
| **Maintainer** | approve anywhere, cut releases | sustained contributions across areas and agreement of the existing maintainers |
| **Lead maintainer** | final say on design and security, manages access | — |

Organizations join as area maintainers: name a team, add it to `CODEOWNERS` for your paths, and keep at least 2 active people on it.

## Guardrails (no role bypasses these)

- `main` accepts changes only through PRs with all required checks green. Nobody pushes directly or force-pushes, maintainers included.
- Security-sensitive paths need maintainer approval: `puenteo/bus.py`, `deliver.py`, `mcp.py`, `server.py`, `install.py`, `redact.py`, `live.py`, `native/`, `.github/`.
- Releases are cut by maintainers from a tag. PyPI publishing runs only from the release workflow.
- An area maintainer who has been inactive for 6 months moves to emeritus, and their access is removed.
- Access can be revoked for abuse, for leaked credentials, or for repeatedly merging changes that break the guardrails.

## Decisions

Consensus in the PR or issue. Where there's disagreement, the lead maintainer decides. Large changes (new interfaces, protocol or schema changes, the plugin API) start as an issue labelled `rfc`.
