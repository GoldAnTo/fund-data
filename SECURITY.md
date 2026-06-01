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
  repository. Use environment variables or `.env` files that are
  themselves gitignored.
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
