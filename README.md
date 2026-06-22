# ICRAS - Intelligent Contract Review and Approval System

ICRAS is a deterministic contract review pipeline for intake, clause extraction, validation, counterparty matching, risk assessment, obligation tracking, approval routing, audit logging, and CLM-ready payload generation.

The system processes a contract bundle, writes every intermediate artifact to a run folder, and produces evidence-backed approval outputs that can be reviewed by legal, finance, compliance, procurement, or downstream workflow systems.

## What The Pipeline Does

ICRAS runs these functional components in order:

1. **Intake** validates the contract bundle, classifies files, and builds the shared context packet.
2. **Evidence Indexing** maps contract text to page-level evidence records.
3. **Clause Extraction** extracts structured clauses, evidence spans, and confidence scores from clean PDFs.
4. **Counterparty Matching** resolves contract party names against vendor master data.
5. **Validation** checks required fields, normalizes values, and detects inconsistencies.
6. **Risk Assessment** scores clauses against playbooks, approval policies, and jurisdiction rules.
7. **Compliance Review** checks GDPR obligations and jurisdiction-specific compliance rules.
8. **Anomaly Detection** flags conflicting terms, suspicious dates, and unusual contract patterns.
9. **Obligation Tracking** produces a structured register of payments, notices, renewals, compliance duties, and other obligations.
10. **Workflow Orchestration** coordinates execution, merges findings, routes exceptions, writes final artifacts, and generates a CLM-ready posting payload.
11. **Jira Posting** optionally creates a tracker issue for non-auto-approved reviews when Jira credentials are configured.

## Repository Layout

```text
.
├── main.py                         # CLI entry point
├── agents/
│   ├── intake/                     # Bundle validation and context creation
│   ├── extraction/                 # PDF text extraction and clause modeling
│   ├── counterparty/               # Party name resolution and vendor matching
│   ├── validation/                 # Required-field and consistency checks
│   ├── risk/                       # Clause and policy risk scoring
│   ├── compliance/                 # GDPR and jurisdiction compliance review
│   ├── anomaly/                    # Conflict and unusual-pattern detection
│   ├── obligation/                 # Obligation register generation
│   └── orchestrator/               # LangGraph workflow and final artifacts
├── schemas/                        # Pydantic v2 artifact schemas
├── utils/                          # Shared artifact, text, date, policy, and run helpers
├── data/
│   ├── bundles/                    # Demo and regression contract bundles
│   ├── extraction_fallbacks/       # Deterministic fallback extraction data
│   └── playbooks/                  # Shared playbook examples
├── policies/                       # Policy documentation and examples
├── tests/                          # Pytest regression suite
├── runs/                           # Generated run folders
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the environment template if you want optional tracing:

```bash
cp .env.example .env
```

LangGraph execution is local. LangSmith tracing is optional and only runs when enabled in `.env`.

## Running ICRAS

Run the full pipeline from the repository root:

```bash
python main.py --bundle data/bundles/clean_nda
```

Useful demo bundles:

```bash
python main.py --bundle data/bundles/clean_nda
python main.py --bundle data/bundles/missing_liability_cap
python main.py --bundle data/bundles/net90_payment
python main.py --bundle data/bundles/net60_policy_demo
```

The CLI prints the selected bundle, step status, final decision, approval route, and generated artifact paths.

## LangChain Showcase

After a pipeline run completes, generate a deterministic approval brief from the
run artifacts with the LangChain Runnable showcase:

```bash
python examples/langchain_approval_brief.py --run-dir runs/<run_id> --trace
```

The example reads `approval_packet.json`, `final_findings.json`, `metrics.json`,
and `context_packet.json`, then writes `approval_brief.md` in the run folder.
By default it uses deterministic LangChain `RunnableLambda` steps and does not
call a model.

To showcase a real LLM call through LangChain, set an OpenAI API key and add
`--llm`:

```bash
export OPENAI_API_KEY=your_openai_api_key
python examples/langchain_approval_brief.py --run-dir runs/<run_id> --llm --trace
```

Use `--model` or `ICRAS_LANGCHAIN_MODEL` to choose a different OpenAI model.
Use `--trace` with LangSmith credentials to inspect the load, fact-selection,
prompt, model, and parsing steps as nested LangChain runs.

## Run Artifacts

Each execution creates a folder under `runs/<run_id>/` with deterministic artifacts:

```text
metadata.json
config.json
audit_log.jsonl
audit_log.md
context_packet.json
document_inventory.json
evidence_index.json
extracted_contract.json
validation_findings.json
counterparty_resolution.json
clause_analysis.json
compliance_findings.json
anomaly_findings.json
obligations.csv
final_findings.json
exceptions.md
approval_packet.json
posting_payload.json
jira_posting_result.json
metrics.json
```

Important outputs:

- `approval_packet.json` contains the final decision, approval route, grouped exceptions, and evidence-backed reasons.
- `exceptions.md` is the human-readable exception summary.
- `posting_payload.json` is a vendor-neutral CLM payload grouped into contract, counterparty, decision, risk, approval, and artifact sections.
- `jira_posting_result.json` records whether Jira posting was created, skipped, disabled, or failed without exposing secrets.
- `audit_log.md` and `audit_log.jsonl` provide traceable execution history.
- `obligations.csv` provides a tabular obligation register for follow-up workflows.

## Optional Jira Posting

Jira posting is disabled by default. To enable it, copy `.env.example` to `.env`
and set:

```bash
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECT_KEY=GEN
JIRA_ISSUE_TYPE=Task
```

When configured, ICRAS creates one Jira issue for `ESCALATE` or `REJECT`
decisions. `AUTO_APPROVE` runs are skipped. Duplicate reruns are also skipped
through the idempotency guard so the same input does not create another tracker
ticket.

Duplicate suppression depends on local completed run history in `runs/`. If
`runs/` is cleared, or if two identical escalated inputs run concurrently, ICRAS
can still create duplicate Jira issues. Jira-side fingerprint search using the
`ICRAS_INPUT_FINGERPRINT` marker is planned as future hardening.

If credentials are missing or Jira returns an error, the contract review still
completes. The outcome is written to `jira_posting_result.json`, summarized in
`metrics.json`, and shown in the CLI/API using only safe fields such as status,
issue key, issue URL, and reason.

## Policy Configuration

Bundle-level policy lives in YAML files such as:

- `approval_policy.yaml`
- `playbook.yaml`
- `jurisdiction_rules.yaml`

Policy changes do not require Python code edits. For example, changing approved payment terms in a bundle from `net-30` to `net-60` changes the next run's approval decision when the contract terms match the updated policy.

## Testing

Run the full regression suite:

```bash
pytest -q
```

Run Ruff:

```bash
ruff check agents tests utils schemas
```

The test suite covers bundle loading, run management, extraction, validation, counterparty matching, risk assessment, obligation generation, orchestration, policy-driven decisions, and end-to-end smoke scenarios.

## Development Notes

- Agent packages expose public APIs through their package `__init__.py` files.
- New code should import from package names such as `agents.validation`, `agents.risk`, and `agents.orchestrator`.
- Shared cross-agent logic belongs in `utils/`.
- Artifact contracts belong in `schemas/`.
- Demo-only integrations belong in `examples/` unless they become part of the deterministic pipeline.
- Run artifacts should remain deterministic and evidence-backed.
- Policy behavior should stay data-driven through YAML wherever possible.

## Optional LangSmith Tracing

To enable LangSmith tracing, set:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=icras
```

Leave `LANGSMITH_TRACING=false` for local deterministic runs without external tracing.
