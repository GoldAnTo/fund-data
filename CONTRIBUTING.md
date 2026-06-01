# Contributing to fund-data

Thanks for taking the time to make `fund-data` better. This document
walks you through local development in the order you'll hit things:
setup → run → test → lint → ship.

## Local setup

```bash
# 1. Clone and create a working virtualenv.
git clone https://github.com/GoldAnTo/fund-data.git
cd fund-data
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Runtime dependencies (AkShare is required at runtime only when
#    you want the AkShare fallback; CI installs it into a separate venv).
pip install -r requirements.txt

# 3. Dev tooling (ruff, black, pre-commit, mypy).
pip install -e ".[dev]"

# 4. Install the pre-commit hooks — they run the same ruff + black
#    invocations as the CI lint workflow.
pre-commit install
```

## Running the skill

```bash
# 75 unit tests, no network required.
cd fund-data && python -m unittest discover scripts/tests

# Smoke test against the public Eastmoney endpoints.
python fund-data/scripts/fund_cli.py list --provider eastmoney --limit 5
python fund-data/scripts/fund_cli.py search 沪深300
python fund-data/scripts/fund_cli.py snapshot 110022

# Doctor — exits non-zero on the first failed check, so it can gate CI.
python fund-data/scripts/doctor.py
```

## Style

- **Formatting**: [`black`](https://github.com/psf/black) with
  `line-length = 100` (matches `pyproject.toml`).
- **Lint**: [`ruff`](https://github.com/astral-sh/ruff) with the rule
  set in `pyproject.toml` (`E + F + W + I + B + UP + C4 + SIM`).
- **Types**: Type hints are required for new code. `mypy` is configured
  in `pyproject.toml`; run `mypy fund-data/scripts/` before opening
  a PR.
- **Imports**: `import` statements go at the top of the file. The
  existing test files use `sys.path.insert(...)` for the
  `scripts/` import — that is allow-listed as `E402` in
  `pyproject.toml` for `tests/` only.
- **Commits**: short, imperative subject line (≤72 chars). Body
  explains the *why*, not the *what*.

## Pull request checklist

A PR is ready for review when:

- [ ] `cd fund-data && python -m unittest discover scripts/tests` is green.
- [ ] `ruff check fund-data/scripts/` is green.
- [ ] `black --check fund-data/scripts/` is green.
- [ ] `mypy fund-data/scripts/` is clean (or has a `#[mypy note]`
      justifying each new `Any`).
- [ ] `pre-commit run --all-files` is clean.
- [ ] `CHANGELOG.md` has an entry under **Unreleased** that explains
      the user-visible change.
- [ ] New commands or flags are documented in `fund-data/SKILL.md`.
- [ ] New providers are documented in `fund-data/PROVIDERS.md`.
- [ ] A test case was added for the new behaviour (or an existing
      one updated).

## Filing issues

- **Bugs**: use the **Bug report** template. Include the exact
  command, the expected output, and the actual output. Attach a
  `doctor.py` dump if the failure is environment-related.
- **Feature requests**: use the **Feature request** template. Spell
  out the use case — "as a [role], I want to [action] so that
  [outcome]".
- **Provider onboarding**: see `fund-data/PROVIDERS.md` first; it
  has a "register your own provider" recipe and the Investoday
  onboarding flow as worked examples.

## Code of conduct

Everyone who contributes is expected to follow
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Be kind, be specific,
assume good faith.

## Release process

Tag-driven. The maintainer bumps the version in `pyproject.toml` and
`fund-data/SKILL.md`, moves the **Unreleased** section of
`CHANGELOG.md` into a dated version header, and pushes the tag:

```bash
# 0.x → 0.y
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

`release.yml` builds the GitHub release from the tag and the
`CHANGELOG.md` body. No PyPI publish in 0.x — install is via the
skill installer (`python3 scripts/install_skill.py install`).
