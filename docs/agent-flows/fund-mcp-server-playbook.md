# Fund MCP Server Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how does
> the `fund-data` MCP server work?", "how do I add a tool?", or
> "why did my client get `INVALID_PARAMS` / `isError: true`?".
> This is the **answer script** for the MCP surface. Pair with
> [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md)
> for diagrams and code anchors.
>
> **Use it when:**
> - Onboarding a new contributor or agent to the MCP surface.
> - Reviewing a PR that touches `fund_mcp.py`, the
>   `TOOLS` list, `TOOL_HANDLERS`, or `handle_message`.
> - Debugging a report of "the agent can't see the tools" or
>   "the agent's call returned no rows" or "the agent's call
>   raised `INVALID_PARAMS`".
> - Fielding a question about MCP compatibility with a
>   specific client (OpenClaw, Codex, Claude Code, custom).
> - Adding a new tool to the catalogue.
>
> **Do NOT use it when:**
> - The question is about a specific tool's data semantics →
>   use [`fund-search-playbook.md`](./fund-search-playbook.md)
>   or [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md).
> - The question is about the underlying Python library →
>   use [`fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md).
> - The question is about installing the MCP server into a
>   specific agent platform → use
>   [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md).

---

## TL;DR (60-second answer)

The `fund-data` MCP server is a **dependency-free, single-file
JSON-RPC 2.0 server over stdin/stdout** that exposes 17 tools
wrapping the local `fund_data` Python library. It has four
protocol methods (`initialize`, `ping`, `tools/list`,
`tools/call`), a fixed tool set (no dynamic registration), and
two error shapes (JSON-RPC errors for protocol violations; tool
results with `isError: true` for application failures). Every
tool except `fund_cloud_status` triggers a cloud bootstrap on
first call if the agent did not pass `db` explicitly.

The defining characteristics are:

- **No dependencies beyond the Python standard library.**
  The server has no third-party packages. If `fund_data` and
  `fund_cloud` import successfully, the server runs.
- **stdio transport only.** The server is a subprocess; the
  client spawns it, talks JSON over stdin/stdout, kills it
  when done. No sockets, no HTTP, no daemon.
- **Capability: tools only.** No resources, no prompts, no
  sampling. The `capabilities` field in `initialize` is
  `{"tools": {"listChanged": false}}`.
- **17 tools, fixed for the life of the process.** Adding a
  tool requires a server restart (the `TOOLS` list is a
  module-level constant).

---

## The full answer template (use this skeleton)

When asked "how does the `fund-data` MCP server work?",
structure the answer in **four paragraphs**, one per layer.
Order matters — it matches the runtime call order from the
client's perspective.

### Paragraph 1 — Transport

> The server is a single Python process that the client spawns
> as a subprocess. Communication is **newline-delimited
> JSON-RPC 2.0 over stdin and stdout** — one JSON object per
> line on each side, no length-prefix framing. The server
> runs a `for line in sys.stdin` loop in `main()` (line 644)
> that parses each line, dispatches via `handle_message`, and
> writes the response to stdout. stderr is free for logs;
> stdout is **strictly** JSON-RPC responses only. An
> accidental `print()` to stdout from a tool handler will
> break every MCP client immediately because the next
> client's line parser will choke on the stray text.

### Paragraph 2 — Protocol

> The server implements four methods. **`initialize`** is
> the first call a client must make; it returns the
> negotiated `protocolVersion` (one of `2024-11-05`,
> `2025-03-26`, `2025-06-18`, `2025-11-25`, with `2025-06-18`
> as the default fallback), the server's `capabilities` —
> `{"tools": {"listChanged": false}}` — the `serverInfo`
> (name `fund-data`, version `0.2.0`), and an `instructions`
> string. **`ping`** returns `{}` for health checks.
> **`tools/list`** returns the 17 tool dicts, each with
> `name`, `description`, and `inputSchema` (the schema
> declares `additionalProperties: false`, so unknown fields
> are rejected with `INVALID_PARAMS`). **`tools/call`** is
> the workhorse — it dispatches to one of the 17
> `TOOL_HANDLERS` based on `params.name`. The server does
> not implement `resources/*`, `prompts/*`, or any
> `notifications/*` from the client.

### Paragraph 3 — Tool dispatch and the cloud bootstrap

> For every `tools/call` except `fund_cloud_status`, the
> server calls `_maybe_bootstrap_cloud(arguments)` before
> the tool handler. The bootstrap is skipped if the agent
> passed `db` in the tool arguments; otherwise it runs
> `fund_cloud.ensure_project_bundle()`, which pulls the
> OSS query bundle (or reuses the cache, or falls back
> gracefully if the network is down — see the search
> playbook Q2 for the full failure policy). The handler
> then runs against the resolved DB path. `TypeError` /
> `ValueError` from the handler become JSON-RPC
> `INVALID_PARAMS` (argument validation failures);
> any other exception becomes a tool result with
> `isError: true` (application failures). Successful
> results return the `content` + `structuredContent`
> triple — see the next paragraph.

### Paragraph 4 — Result envelope

> Every successful `tools/call` response has three fields.
> **`content`** is a list with one text block:
> `{"type": "text", "text": "<JSON dump of the payload>"}`.
> For list payloads (e.g. `fund_search` rows), the dump
> is `json.dumps(rows, ensure_ascii=False, indent=2)` —
> human-readable, with Chinese fund names rendered as-is.
> **`structuredContent`** is the machine-typed payload:
> for lists it is `{"rows": [...], "count": N}`; for
> dicts it is the dict itself. **`isError`** is the
> success flag — `true` for application errors, with
> the `text` field carrying `{"error": "<message>"}`.
> Clients that respect the MCP spec can read
> `structuredContent` directly without re-parsing the
> text. Clients that map `isError: true` to a JSON-RPC
> retry will get into a loop, because the next call
> returns the same error.

---

## The 12 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they appear
here, with the same level of detail** — these are the
explanations the team has settled on after multiple rounds of
"but why?".

### Q1. Why is the server a single stdio process with no dependencies?

- **The MCP stdio transport is the universal baseline.** Every
  MCP-capable client (OpenClaw, Codex, Claude Code, the
  reference SDKs) knows how to spawn a subprocess and pipe
  JSON. A stdio server "just works" everywhere; a socket
  server needs per-platform plumbing.
- **No dependencies means no version drift.** The server
  imports only `json`, `sys`, `pathlib`, `typing`, and the
  in-tree `fund_data` / `fund_cloud` modules. An
  OpenClaw on Python 3.11 and a Codex on Python 3.13 run
  the same server. There is no `pip install mcp` step.
- **The trade-off is no HTTP / SSE transport.** A remote
  agent that cannot spawn a local subprocess has no way
  to call the server. The team is tracking a Streamable
  HTTP wrapper for v0.3.0.

### Q2. Why are there two error shapes (JSON-RPC error vs `isError: true`)?

- **JSON-RPC errors are for protocol violations.** The
  client sent something the server cannot interpret —
  a parse error, an unknown method, a missing required
  field, a type mismatch. The client should fix the
  call, not retry it. The error code (`-32700` /
  `-32600` / `-32601` / `-32602` / `-32603`) is
  standard JSON-RPC and most clients map it to
  "developer error".
- **`isError: true` is for application failures.** The
  client sent a valid call and the server tried to
  honour it; the call failed because the provider chain
  raised, or the SQLite write failed, or the network
  was down. The client should decide whether to retry
  (often yes — a network blip is transient) or to
  surface the error (often yes — a missing fund is not
  transient).
- **Mixing the two would force one policy on the other.**
  A client that auto-retries JSON-RPC errors will spam
  the server with malformed calls. A client that does
  not retry `isError: true` will lose data on transient
  blips. The two shapes let each client pick the
  policy that fits.

### Q3. Why does every tool except `fund_cloud_status` trigger the cloud bootstrap?

- **The bootstrap is "free infrastructure".** The
  `fund_data_query.sqlite.gz` bundle on OSS is the team's
  effort to save agents from running a 21-hour AkShare
  backfill on first install. If the bundle is reachable,
  the agent gets a pre-populated DB in seconds. If not,
  the agent falls back to live providers.
- **The trigger is per-tool, not per-process.** Each
  tool call independently decides whether to bootstrap,
  based on whether `db` was passed. An agent that wants
  to pin the DB once and reuse it should pass `db` on
  every call; the cost of repeating the argument is
  cheap and the cost of an accidental cache write is
  high.
- **`fund_cloud_status` does not trigger the bootstrap
  because it *is* the bootstrap introspection.** A
  client that wants to inspect the cache state before
  any other call should call `fund_cloud_status` first;
  triggering the bootstrap to answer a question about
  the bootstrap would be circular. The cache directory
  is queried directly via `fund_cloud.status()`.

### Q4. Why is `capabilities.tools.listChanged` set to `false`?

- **The tool set is a module-level constant.** `TOOLS`
  in `fund_mcp.py:98` is a list literal; the only way
  to change it is to restart the server process. A
  client that gets `listChanged: true` would expect
  `notifications/tools/list_changed` and poll
  `tools/list`; neither is implemented.
- **The honest signal is more useful than a false
  promise.** A client that respects `listChanged`
  optimistically would call `tools/list` repeatedly
  for no benefit. A client that respects it
  pessimistically (or ignores it) gets the same
  answer. `false` is the contract.
- **The team has not ruled out dynamic registration.**
  If a use case emerges for "register a custom tool
  at runtime", the `listChanged` flag flips to `true`
  and the server starts emitting notifications. Until
  then, the simpler contract is the right one.

### Q5. Why is the protocol version negotiation "first supported wins", not "use the latest"?

- **The client may be older than the server.** A
  Codex from March 2025 only knows `2025-03-26`; if
  the server forced `2025-11-25`, the client would
  send messages the server cannot parse. The
  negotiation lets the older client use its preferred
  version as long as the server supports it.
- **The fallback is `2025-06-18`.** That is the
  version most clients implement as of late 2025; a
  client that asks for an unknown version gets the
  fallback rather than an error. The client should
  then honour the returned version for subsequent
  calls.
- **The four supported versions
  (`2024-11-05` / `2025-03-26` / `2025-06-18` /
  `2025-11-25`) cover every shipped client.** The
  team has not had to bump the support set since
  the server was written; a new client that
  requests `2026-03-01` would get the `2025-06-18`
  fallback and work, but the team should add
  `2026-03-01` to `SUPPORTED_PROTOCOL_VERSIONS`
  for the next release.

### Q6. Why are tool input schemas `additionalProperties: false`?

- **The server rejects typos.** An agent that
  types `keywords` instead of `keyword` gets
  `JSONRPC_INVALID_PARAMS: ...` instead of a silent
  empty result. The strict schema is a developer
  ergonomics win for the agent author.
- **The cost is forward compatibility.** Adding a
  new field to a tool's schema is technically a
  breaking change for clients that relied on the
  old `additionalProperties: true` shape. The
  team accepts this because the tool set is small
  (17 tools) and the changelog is the source of
  truth for what changed.
- **`additionalProperties: false` is also the
  MCP spec recommendation.** A client that
  reads the schema and builds a validator
  benefits from the explicit allow-list.

### Q7. Why does the server not implement `resources/list` or `prompts/list`?

- **The team has not needed them yet.** The 17
  tools cover the agent's data-plane needs; the
  resource shape (`fund://funds/110022` URIs) is
  a sugar layer on top of `fund_export` that the
  team has not built. Prompts are an even
  bigger layer (templating, partials,
  placeholders) that is out of scope for v0.2.0.
- **The `capabilities` field is the honest
  signal.** A client that asks for `resources/list`
  gets a `METHOD_NOT_FOUND` error rather than a
  misleading empty list. The client should not
  infer "the server has no resources" from a
  missing call — it should infer "the server does
  not implement resources" from `capabilities`.
- **Adding resources or prompts is a clear
  next step.** A `fund://funds/{code}` resource
  URI mapping to `fund_export(table="funds",
  fund_code=code)` is a 30-line change. A
  `fund-compare` prompt that fetches three
  funds' snapshots and asks the LLM to compare
  them is a 50-line change. Both are tracked
  under v0.3.0 backlog.

### Q8. Why is `content[0].text` a JSON dump, not a markdown table?

- **JSON is the universal interchange format.**
  Every MCP client knows how to render JSON; not
  every client knows how to render a markdown
  table. The text is always valid JSON, regardless
  of the payload shape.
- **`structuredContent` is the typed payload for
  clients that want to render something other
  than JSON.** A client that wants to display a
  table can read `structuredContent.rows` and
  format it. A client that wants to display JSON
  can read `content[0].text` directly. Both
  paths are supported.
- **The dump uses `ensure_ascii=False`.** Chinese
  fund names are rendered as-is, not as `\uXXXX`
  escapes. A client that assumes ASCII will
  misparse; the contract is UTF-8.

### Q9. Why does `tools/call` only catch `TypeError` and `ValueError` as `INVALID_PARAMS`?

- **`TypeError` is the most common argument
  validation failure.** A tool that expects a
  string but gets `None` raises `TypeError`; a
  tool that expects a list but gets a dict raises
  `TypeError`. These are the agent's fault, not
  the server's.
- **`ValueError` is the second most common.**
  A tool that expects a 6-digit fund code but
  gets `"abc"` raises `ValueError("fund code
  must contain 6 digits")`. Again, the agent's
  fault.
- **Any other exception is an application
  error.** `KeyError` from a missing column in
  a SQL response, `sqlite3.OperationalError` from
  a locked DB, `ProviderError` from the chain —
  these all become `isError: true` tool results.
  The client decides whether to retry.

### Q10. Why does the server not stream results for large tables?

- **Stdio is line-buffered.** The contract is
  one JSON object per line. Streaming would mean
  either multiple lines per result (which breaks
  the line-as-message-boundary contract) or
  multiple `content` blocks in a single response
  (which the spec discourages for `tools/call`).
- **`fund_export` accepts a `limit` argument.**
  An agent that wants to walk a 26k-fund table
  should call `fund_export` in chunks of 1000,
  not ask for the whole table in one call.
  The `count` field in `structuredContent` lets
  the agent know how many rows it has.
- **The team has not seen a use case for
  streaming.** The largest realistic call is
  `fund_coverage_report` over 26k funds; the
  current call returns ~26k small rows, which
  serialises to ~3 MB of JSON. The client can
  handle that without streaming. A future
  `notifications/progress` channel would help
  the agent know the call is still working, but
  it would not change the result delivery shape.

### Q11. Why does the server not authenticate the client?

- **The server is a local subprocess.** The
  client that spawns the server is the one
  with local machine access; an attacker that
  can talk to the server's stdin already has
  local machine access. There is no remote
  trust boundary to enforce.
- **The data is public.** The fund universe,
  NAV history, and holdings are all public
  information available from Eastmoney and
  AkShare. There is no secret material in
  the response payloads.
- **Provider keys are the user's, not the
  server's.** `INVESTODAY_API_KEY` and
  `TUSHARE_TOKEN` live in the server's
  environment, not in the request. An agent
  that has the server's environment inherits
  the keys; an agent that does not has
  read-only access to the no-key providers.
  This is the right trust model: the user
  controls the keys, the agent gets what the
  user has paid for.
- **A future HTTP transport would need auth.**
  Tracked under v0.3.0; the auth scheme will
  likely be a per-session bearer token, with
  the token shared out-of-band by the user.

### Q12. Why does the server not implement `notifications/cancelled`?

- **The server is synchronous.** A `tools/call`
  blocks until the handler returns; the server
  cannot accept a `notifications/cancelled` while
  it is blocked.
- **The workarounds are at the client layer.**
  A client that wants to cancel a long call
  closes the server's stdin, which terminates
  the subprocess. The server returns 0 from
  `main()` on EOF; the client cleans up.
- **The spec allows this.** Synchronous servers
  are explicitly allowed to ignore
  `notifications/cancelled`; the expectation is
  that an async server would respect them. When
  the team adds a Streamable HTTP transport, the
  async server will implement cancellation.

---

## Design philosophy (the "why" of the four-method shape)

Read this section once and the rest of the playbook becomes
obvious.

1. **stdio is the universal baseline.** Every MCP client
   knows how to spawn a subprocess. The server that uses
   stdio "just works" everywhere; the server that uses
   sockets needs per-platform plumbing. The team's
   philosophy is "the simplest transport that works for
   every client" — stdio wins.
2. **No dependencies means no version drift.** The server
   imports only the Python standard library and the
   in-tree `fund_data` / `fund_cloud`. There is no
   `pip install mcp` step, no version pin, no breakage
   on Python minor bumps. The cost is no third-party
   helpers (e.g. `pydantic` for schema validation); the
   benefit is zero install.
3. **Two error shapes for two failure policies.** JSON-RPC
   errors are for protocol violations (developer fixes);
   `isError: true` is for application failures (client
   retries). Conflating them would force one policy on
   both. The two shapes let each client decide.
4. **The cloud bootstrap is a per-call decision, not a
   per-process one.** An agent that wants to pin the DB
   passes `db` on every call; an agent that wants the
   cache default omits it. The server's `_maybe_bootstrap_cloud`
   runs every time and is cheap (~1 ms on a cache hit).
5. **Capabilities is the honest signal.** The server
   declares exactly what it implements (`tools`,
   `listChanged: false`). A client that asks for
   resources gets a `METHOD_NOT_FOUND` and can decide
   whether to fall back. The `capabilities` field is
   the contract.
6. **`content[0].text` is the universal payload,
   `structuredContent` is the typed payload.** A client
   that only renders text reads the text; a client
   that wants the typed payload reads
   `structuredContent`. Both are populated on every
   successful call; neither is preferred.
7. **The 17-tool set is fixed for the life of the
   process.** Adding a tool is a code change and a
   server restart. The team has not built a
   "register a custom tool at runtime" API; if a use
   case emerges, `listChanged` flips to `true` and
   the server emits notifications. Until then, the
   static tool set is the right shape.

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR
reviews and support threads. Avoid them.

- **"The server is async."** It is not. The
  `for line in sys.stdin` loop is a blocking
  readline; a long tool call blocks the loop. A
  client that assumes async can pipe multiple
  calls in parallel will see them serialised.
- **"It supports HTTP."** It does not. Stdio only.
  A future Streamable HTTP transport is tracked
  under v0.3.0; do not promise it today.
- **"It supports resources and prompts."** It does
  not. `capabilities` is `{"tools": {}}`; a client
  that asks for `resources/list` gets
  `METHOD_NOT_FOUND`.
- **"Tool results are JSON."** They are a triple:
  `content` (text JSON dump) + `structuredContent`
  (typed payload) + `isError` (success flag).
  Conflating the three is a common client bug.
- **"`isError: true` means the call failed at the
  protocol level."** No, it means the call
  succeeded at the protocol level and the tool
  raised at the application level. A JSON-RPC
  error is what protocol failures look like.
- **"The server has a `fund_doctor` tool."** It
  does not. The v0.3.0 backlog has it; until
  then, an agent that wants a health check has
  to shell out to `fund-cli doctor`.
- **"The server streams results."** It does not.
  One JSON object per response, no chunked
  encoding, no `notifications/progress`. An agent
  that walks a large table should call
  `fund_export` in chunks of 1000.
- **"The protocol version is hard-coded."** It
  is negotiated in `initialize`. The server
  advertises support for four versions
  (`2024-11-05` / `2025-03-26` / `2025-06-18` /
  `2025-11-25`) and falls back to `2025-06-18`
  if the client asks for an unknown version.
- **"The cloud bootstrap is a one-time thing."
  It is per-call. Each tool call (except
  `fund_cloud_status`) re-runs the bootstrap
  decision; passing `db` skips it, omitting it
  re-checks the cache.

---

## How to add a new tool (the contributor recipe)

When a new capability lands in `fund_data.py` and the team
wants to expose it as an MCP tool:

1. **Add a Python helper** to `fund_data.py` if it does not
   exist. The helper must accept `db_path` and `provider` as
   keyword arguments (or via the existing fetch convention).
2. **Add a tool dict** to `TOOLS` in `fund_mcp.py`:
   ```python
   _tool(
       "fund_new_thing",
       "Description that explains the agent use case, not the implementation.",
       {
           **COMMON_ARGS,
           "code": _string_schema("6-digit fund code."),
           # ... other args
       },
       required=["code"],
   ),
   ```
3. **Add a `_call_fund_new_thing` handler** in
   `fund_mcp.py` that calls the Python helper. The
   handler signature is `(arguments: dict) -> Any`.
4. **Register the handler** in `TOOL_HANDLERS`:
   ```python
   "fund_new_thing": _call_fund_new_thing,
   ```
5. **Update `SKILL.md` and the `install_skill.py` skill
   manifest** if the tool needs a new frontmatter field.
6. **Add a unit test** in `fund-data/scripts/tests/` that
   exercises the handler with a fake fund_data module.
7. **Bump `SERVER_VERSION`** in `fund_mcp.py:27` if the
   tool is a breaking change for clients.

If a tool does not need the cloud bootstrap (e.g. a
pure-cache inspection), add it to the exception list
in `handle_message` (`tool_name != "fund_cloud_status"`).

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not the
live code. When the code changes, update the playbook in
the same PR. The check is:

- A new tool is added to `TOOLS` → update Q3 catalogue
  and the contributor recipe.
- A new MCP method is implemented → update Q2 (error
  shapes) and Q7 (capabilities).
- A new error code is introduced → update Q2 and the
  protocol table.
- The bootstrap behaviour on a specific tool changes →
  update Q3.
- Protocol version set changes → update Q5.
- A new client integrates → update the gateway config
  examples in the related playbook.

If a PR changes any of the above and does not update the
playbook, request changes with a pointer to this section.

---

## Related documents

- [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) —
  diagrams + code anchors + tool catalogue.
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —
  what happens *inside* a tool call (cloud bootstrap +
  provider chain).
- [`fund-search-playbook.md`](./fund-search-playbook.md) —
  the single-search answer script.
- [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md) —
  the batch-sync answer script (for `fund_sync` /
  `fund_batch_sync` tools).
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest.
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —
  per-platform install layout for Codex / Claude /
  OpenClaw; the MCP server config block in
  §"MCP server" is the canonical gateway snippet.
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items (HTTP/SSE transport,
  progress notifications, resources, prompts, doctor
  tool).
