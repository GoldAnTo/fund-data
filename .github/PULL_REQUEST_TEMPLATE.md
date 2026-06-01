name: Pull request
description: Use this template for any non-trivial change to the project
labels: []
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the PR. The checklist below mirrors
        `CONTRIBUTING.md` — make sure every box is honest.

  - type: input
    id: related
    attributes:
      label: Related issue / discussion
      description: |
        `Fixes #123`, `Closes #45`, or `Relates to #67` if relevant.
        Leave blank for standalone changes.
    validations:
      required: false

  - type: textarea
    id: summary
    attributes:
      label: Summary of the change
      description: |
        One paragraph: what you changed, why, and what the user sees.
    validations:
      required: true

  - type: textarea
    id: testing
    attributes:
      label: How was it tested?
      description: |
        Which commands you ran and what they printed. The README
        already has the canonical commands — copy from there.
    validations:
      required: true

  - type: checkboxes
    id: checks
    attributes:
      label: Local checks
      options:
        - label: |
            `cd fund-data && python -m unittest discover scripts/tests`
            is green
        - label: |
            `ruff check fund-data/scripts/` is green
        - label: |
            `black --check fund-data/scripts/` is green
        - label: |
            `pre-commit run --all-files` is clean
        - label: |
            `CHANGELOG.md` has an entry under **Unreleased**

  - type: checkboxes
    id: docs
    attributes:
      label: Documentation impact
      options:
        - label: |
            New commands / flags are documented in `fund-data/SKILL.md`
        - label: |
            New providers are documented in `fund-data/PROVIDERS.md`
        - label: |
            A test case was added (or an existing one updated)

  - type: textarea
    id: risk
    attributes:
      label: Risk and rollback
      description: |
        One paragraph on what could break and how to revert. The
        nightl­y backfill is the only piece that can touch a
        production-shaped data base; mention if your change does.
    validations:
      required: false
