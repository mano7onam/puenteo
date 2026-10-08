# Contributing to puenteo

Everyone is welcome: individuals, companies and AI agents. Every change goes through the same gates, so you don't have to trust anyone's good intentions.

## Fastest paths

- **New agent store, MCP tool, delivery channel or live detector?** Write a plugin ([docs/PLUGINS.md](docs/PLUGINS.md)). You don't need anyone's permission, and you can ship it as your own package.
- **Fix or feature in the core?** Open a PR against `main`.

## Every PR must pass

1. **CI**: hermetic tests on Linux, macOS and Windows with Python 3.9, 3.12 and 3.13, plus the Rust≡Python equivalence check when parsers change.
2. **Checks**:
   - **Zero runtime dependencies**: the core may use only the stdlib.
   - **Security lint**: Bandit, at high confidence.
   - **Secret scan**: gitleaks.
   - **No edits to vendored agent data**: tests must use fixtures, never real transcripts.
3. **DCO sign-off** on every commit (`git commit -s`). By signing off you certify the [Developer Certificate of Origin](https://developercertificate.org/), i.e. that you have the right to submit the code.
4. **Review by a code owner** (see `.github/CODEOWNERS`). Security-sensitive areas (bus, delivery, hooks, install, redaction, MCP/HTTP servers) always need a maintainer.
5. Tests for the change. Bug fixes need a regression test that fails without the fix.

`main` is protected: no direct pushes and no force-pushes, linear history, and all of the checks above required.

## AI agents as contributors

Agent-authored PRs are welcome, with these conditions:
- Say so in the PR description: which agent and model, and which human is responsible for the change.
- The human sponsor signs off on the commits (DCO) and answers review comments.
- Agent PRs go through the same gates. A green CI result is necessary, but it isn't enough on its own.
- Don't paste transcripts, secrets or customer data into issues, PRs or fixtures.

## Trust levels

See [GOVERNANCE.md](GOVERNANCE.md). In short: anyone can contribute through PRs. Contributors with a track record become **trusted maintainers of an area** (a provider, a plugin, docs). They can approve and merge in that area, and **organizations** can hold that role for their own integrations.

## Dev setup

```bash
git clone https://github.com/mano7onam/puenteo && cd puenteo
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
# optional Rust core:  cd native && maturin develop --release
```

Releases: bump `puenteo/version.py` + `pyproject.toml` and run `scripts/sync_skills.sh`. Bump `native/Cargo.toml` and `native/pyproject.toml` **only** when `native/` changes. The core and the accelerator are versioned independently, and `puenteo[fast]` requires a compatible minimum.

Conventions: match the surrounding code, don't add dependencies, run `scripts/sync_skills.sh` after editing a SKILL.md, and keep commit subjects in the `area: what changed` form.
