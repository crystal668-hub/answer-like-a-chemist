# Documentation Rules

These rules apply to all maintained project documents under `docs/`.
Read `../GLOBAL_DEV_SPEC.md` before changing project documentation. Current code
is the source of truth; `GLOBAL_DEV_SPEC.md` is the sole current-system overview.

## Classification

Read the document's purpose, main sections, and conclusions before choosing a
directory. Classify by primary reader intent, not by its existing location,
filename suffix, author, tool, or completion status.

| Directory | Primary purpose | Typical contents |
| --- | --- | --- |
| `design/` | Explain what the system should do and why | Architecture, specifications, interfaces, invariants, data contracts, tradeoffs, acceptance criteria |
| `plan/` | Explain how to carry out or diagnose a change | Ordered tasks, file changes, investigation steps, implementation phases, test and rollout plans |
| `handoff/` | Let another maintainer take over | Baseline, issue inventory, completed work, remaining work, dependencies, next actions, acceptance expectations |
| `report/` | Record what happened and what evidence proves | Benchmark results, reviews, incident findings, implementation validation, test outcomes and limitations |
| `guide/` | Explain how to operate an existing capability | Setup, commands, usage, runbooks, troubleshooting and API usage |
| `research/` | Investigate options before committing to a design | Literature, external evidence, feasibility, comparisons, candidate selection and recommendations |

Use these six singular, lowercase category names. Do not create tool-, plugin-,
agent-, or workflow-brand directories, alternate plural categories, or catch-all
folders. Keep files directly in the category until volume justifies a topic
subdirectory; document any new category or nesting convention here first.

For mixed documents, use the dominant purpose. Specifications with an
implementation sequence still belong in `design/`; task lists with brief
architecture notes belong in `plan/`. A diagnosis plan belongs in `plan/`,
while completed diagnosis findings belong in `report/`. Research recommendations
belong in `research/` until they become an adopted design. Prefer a primary
document plus links over duplicating the same content across categories.

## Naming and Status

- Keep only `README.md` and `AGENTS.md` directly under `docs/`.
- Name new dated designs, plans, handoffs, reports, and research documents
  `YYYY-MM-DD-topic[-document-kind].md`. Use lowercase ASCII kebab-case for the
  topic and concise, meaningful names. Do not invent dates for existing files.
- Name continuously maintained guides `topic-usage.md` or `topic-runbook.md`.
- Preserve existing basenames during category-only migrations unless there is a
  collision or an explicitly requested rename.
- New dated documents must identify their date, scope, and status near the top.
  Reports must identify the evidence baseline and verification limits; handoffs
  must distinguish completed work from outstanding work.
- Completion is metadata, not a category: a completed plan stays in `plan/`,
  and a closed handoff stays in `handoff/`. Add a linked report for acceptance
  evidence. Do not move completed documents into a generic archive folder.
- Retain historical decisions, results, and original evidence. Add explicit
  supersession notes with links when appropriate; do not silently rewrite old
  proposals as implemented behavior. Check current code and
  `GLOBAL_DEV_SPEC.md` before treating any historical document as current.

## Index, Links, and Retention

- Add every maintained document to `docs/README.md` under its primary category,
  with a short description. Do not duplicate the classification rules there.
- Use relative Markdown links for navigation, resolved from the containing file.
  Repository-relative paths in commands or code references resolve from the
  canonical project root; keep that context explicit.
- When moving a document, update inbound and outbound links, inline path
  references, `README.md`, and the index in `GLOBAL_DEV_SPEC.md`. Search tracked
  and ignored local documentation so references are not missed.
- Validate that all catalog targets and previously valid local links still
  resolve, every original document is retained, and obsolete directories and
  tool-specific execution boilerplate are absent. Do not add redirect copies or
  symlinks for an obsolete document layout.
- Mark already missing historical references and external-project paths as such;
  do not invent replacement documents or imply those paths exist locally.
  If historical machine paths are replaced with placeholders, label that change
  and require resolving the actual path before running an example command.
- Maintained documents and this file belong in Git. Do not blanket-ignore
  `docs/` or any category. Keep operating-system metadata ignored.
- Store raw benchmark runs, transcripts, generated outputs, and temporary
  evidence under `state/benchmark-runs/` following project runtime conventions.
  A curated report may live in `report/` and link to those artifacts.
- After changes to system structure or behavior, update the appropriate current
  section of `GLOBAL_DEV_SPEC.md`. For a documentation-only reorganization,
  update its directory description and references and validate links; application
  tests are needed only if executable code or runtime behavior also changes.
