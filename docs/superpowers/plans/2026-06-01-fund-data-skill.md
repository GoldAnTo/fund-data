# Fund Data Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local `fund-data` Codex skill with callable Python functions and SQLite persistence for Chinese fund data.

**Architecture:** Keep `SKILL.md` as the agent entrypoint and put deterministic behavior in `scripts/fund_data.py` plus `scripts/fund_cli.py`. Tests use static Eastmoney payloads so parsing and persistence are verifiable without network access.

**Tech Stack:** Python 3 standard library, SQLite, unittest, Codex skill folder layout.

---

## File Structure

- Create `fund-data/SKILL.md`: skill trigger description and workflow.
- Create `fund-data/references/schema.md`: table and field contracts.
- Create `fund-data/scripts/fund_data.py`: source clients, parsers, SQLite store, sync helpers.
- Create `fund-data/scripts/fund_cli.py`: CLI wrapper for search, nav, snapshot, sync, export.
- Create `fund-data/scripts/tests/test_fund_data.py`: parser and persistence tests.

## Tasks

### Task 1: Initialize Skill Skeleton

- [ ] Run `init_skill.py fund-data --path /Users/xiongjiali/Desktop/code/fundData --resources scripts,references`.
- [ ] Remove placeholder text from generated files.
- [ ] Confirm `fund-data/agents/openai.yaml` exists.

### Task 2: Parser and Store Tests

- [ ] Add offline fixtures for search JSON, NAV HTML, and snapshot JS inside the test file.
- [ ] Add tests for fund search parsing, NAV parsing, snapshot parsing, SQLite upserts, and raw response recording.
- [ ] Run `python3 -m unittest fund-data/scripts/tests/test_fund_data.py` and verify the tests fail because implementation is missing.

### Task 3: Core Library

- [ ] Implement `FundDataClient` using `urllib`.
- [ ] Implement `FundDataStore` using `sqlite3`.
- [ ] Implement parser functions that accept raw payload strings.
- [ ] Implement high-level helpers `search_funds`, `fetch_nav_history`, `fetch_snapshot`, `sync_fund`, and `export_table`.
- [ ] Run unit tests until they pass.

### Task 4: CLI

- [ ] Implement `search`, `nav`, `snapshot`, `sync`, and `export` subcommands.
- [ ] Add offline CLI tests by passing payload files where appropriate.
- [ ] Run unit tests and a local CLI smoke command.

### Task 5: Skill Docs and Install

- [ ] Write `SKILL.md` with activation scenarios, workflow, commands, persistence rules, and safety notes.
- [ ] Write `references/schema.md`.
- [ ] Run `quick_validate.py` on the skill folder.
- [ ] Copy the skill folder to `/Users/xiongjiali/.codex/skills/fund-data`.
- [ ] Run validation on the installed copy.

