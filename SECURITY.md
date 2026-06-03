# Security policy

## Reporting a vulnerability

If you discover a security issue in `fund-data`, please **do not
open a public GitHub issue**. Instead, report it privately:

- **GitHub private vulnerability report** (preferred): use the
  [Report a vulnerability](https://github.com/GoldAnTo/fund-data/security/advisories/new)
  button on the **Security** tab. Only the maintainer can see the
  report.
- **Email**: if you cannot use GitHub's private reporting, email
  the maintainer — see the commit history for an address.

Please include:

- A short description of the vulnerability and its impact.
- The exact command, file, or endpoint that exhibits it.
- A reproducer (commands, sample request, or code snippet).
- The version / commit hash you observed it on.

## What to expect

| Stage | SLA |
|---|---|
| Acknowledge your report | within 3 business days |
| Triage and severity assessment | within 7 business days |
| Patch for **critical** / **high** issues | within 30 days |
| Public disclosure | coordinated with you, after a patch is shipped |

## Out of scope

- Bugs in the upstream **AkShare**, **Eastmoney**, **Tushare**, or
  **Investoday** providers. Report those to the respective
  maintainer.
- Issues that require social engineering or physical access.
- Rate limiting / DDoS considerations on the public Eastmoney
  endpoints. Use the CLI defaults and respect
  `--min-interval-seconds`.

## Best practices for users of this skill

- Never commit a `TUSHARE_TOKEN` or `INVESTODAY_API_KEY` to the
  repository. Use environment variables or the project-root
  `.env` file (the loader in `fund_data._env` reads it at
  entry-point start; `.gitignore` keeps it out of git).
- The canonical token name is **`INVESTODAY_API_KEY`**
  (documented in `fund-data/PROVIDERS.md`, `SKILL.md`, and
  `README.md`); the legacy **`INVESTDATA_API_KEY`** is still
  accepted as a fallback. As of 2026-06-03, `doctor.py`
  recognises both and a freshly configured canonical key is
  no longer mis-reported as "not set" — see commit history
  for the regression test.
- Shell exports win over the file. `fund_data._env.load_env`
  uses `os.environ.setdefault` semantics, so a CI runner /
  `direnv` / `export INVESTODAY_API_KEY=...` always takes
  precedence over the local `.env` fallback.
- The project intentionally does not ship an `.env.example`
  template (`.gitignore` blocks `.env*` without an allowlist).
  The rationale: any template that lands in the repo is one
  copy-paste away from leaking a real key. Operators who need
  a checklist find the schema in `fund-data/PROVIDERS.md` /
  `SKILL.md` / `README.md`.
- The SQLite database is **gitignored** for a reason — it can
  contain raw upstream responses that may include the IP of the
  calling machine in HTTP headers. Treat `data/fund_data.sqlite`
  as a local artifact.
- Fund data is research-only. Do not use it as personalized
  investment advice. Always report the `source` column and
  `fetched_at` timestamp when quoting numbers.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x (current 0.x) | yes |
| < 0.1.0 | no |
