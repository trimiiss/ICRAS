# ICRAS — Intelligent Contract Review and Approval System

ICRAS is a multi-agent AI pipeline that automates the review, risk assessment, and approval of legal contracts. It ingests a **contract bundle** (PDF contract + supporting policy files), runs it through a chain of specialized agents, and produces a structured **approval packet** with findings, risk scores, and recommendations.

> **Sprint 2 Status:** Agent H now orchestrates the deterministic pipeline with LangGraph. LLM-backed reasoning can be added in later stories without changing the file-based artifact contract.

---

## Folder Structure

```
icras/
├── main.py                  # CLI entry point
├── agents/                  # Deterministic pipeline agents
│   ├── intake_agent.py
│   ├── extraction_agent.py
│   ├── counterparty_agent.py
│   ├── validation_agent.py
│   ├── risk_agent.py
│   └── orchestrator_agent.py
├── schemas/                 # Pydantic v2 data models
│   ├── common.py
│   ├── context_packet.py
│   ├── extracted_clause.py
│   ├── finding.py
│   ├── risk_result.py
│   └── approval_packet.py
├── policies/                # Policy templates (future)
├── data/
│   ├── bundles/             # Sample contract bundles
│   │   ├── clean_nda/
│   │   └── services_agreement/
│   ├── vendor_master.csv
│   └── playbooks/
├── runs/                    # Generated run folders (gitignored)
├── tests/                   # Pytest test suite
├── utils/
│   ├── bundle_loader.py     # Bundle validation and loading
│   └── run_manager.py       # Deterministic run folder creation
├── .env.example
├── .gitignore
├── AGENTS.md
├── README.md
└── requirements.txt
```

---

## Setup (Windows PowerShell)

### 1. Clone and enter the project

```powershell
cd C:\Users\Admin\Desktop\ICRAS
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Copy environment template

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in your API keys when needed. LangGraph runs locally
without an API key; LangSmith tracing uses `LANGSMITH_API_KEY` when enabled.

---

## Running the Pipeline

### One-command demo

Run this from the repository root:

```powershell
python main.py --bundle data/bundles/net90_services_agreement
```

The command prints the selected bundle, every agent step with pass/fail status,
the final decision, the approval route, and generated artifact paths.

### Presenter demo flow

```powershell
python main.py --bundle data/bundles/clean_nda
```

Expected result: `Final Decision: AUTO-APPROVE`.

```powershell
python main.py --bundle data/bundles/missing_liability_cap
```

Expected result: `Final Decision: ESCALATE` with `Legal review`.

```powershell
python main.py --bundle data/bundles/net90_payment
```

Expected result: `Final Decision: ESCALATE` with `Finance approval`.

### Live policy-change demo

The live policy demo uses `data/bundles/net60_policy_demo`, which contains
net-60 payment terms while the default policy allows only net-30.

```powershell
python main.py --bundle data/bundles/net60_policy_demo
```

Expected result before editing policy: `Finance approval`.

Edit `data/bundles/net60_policy_demo/approval_policy.yaml` and change:

```yaml
approved_payment_terms:
  terms:
  - net-30
```

to:

```yaml
approved_payment_terms:
  terms:
  - net-60
```

Then re-run:

```powershell
python main.py --bundle data/bundles/net60_policy_demo
```

Expected result after editing policy: `Final Decision: AUTO-APPROVE`.

Each run creates a unique folder under `runs/` with this structure:

```
runs/<run_id>/
├── metadata.json
├── config.json
├── audit_log.jsonl
├── audit_log.md
├── context_packet.json
├── document_inventory.json
├── evidence_index.json
├── extracted_contract.json
├── validation_findings.json
├── counterparty_resolution.json
├── clause_analysis.json
├── obligations.csv
├── final_findings.json
├── exceptions.md
├── approval_packet.json
├── posting_payload.json
└── metrics.json
```

Running the same bundle multiple times produces separate run folders.

`approval_packet.json` includes grouped approval routes plus per-exception
triage items with category, approver, reason, next action, and evidence links.
The route table is configured in each bundle's `approval_policy.yaml` under
`exception_routing`, so approver routing can change without Python code edits.

`posting_payload.json` is a vendor-neutral CLM integration payload. It is
validated by Pydantic and grouped into `contract`, `counterparty`, `decision`,
`risk`, `approval`, and structured `artifacts` sections so a mock CLM adapter
can consume it without parsing the internal run artifacts first.

---

## Running Tests

```powershell
pytest -q
```

Or with verbose output:

```powershell
pytest -v
```

---

## Configurable Policy Rules

Contract policy thresholds live in each bundle's `approval_policy.yaml`.
For example, change:

```yaml
approved_payment_terms:
  terms:
    - net-30
```

to:

```yaml
approved_payment_terms:
  terms:
    - net-60
```

The next bundle load uses the edited YAML without Python code changes. The test
`tests/test_policy_rules.py::test_full_pipeline_policy_edit_changes_demo_decision`
demonstrates the visible full-pipeline decision change: a net-60 payment clause
routes to Finance under net-30 policy and auto-approves after the YAML is
changed to net-60.

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Bundle** | A folder containing a contract PDF and supporting YAML/CSV policy files |
| **Run** | A single pipeline execution, identified by a unique `run_id` |
| **Schema** | A Pydantic v2 model that validates structured data between agents |
| **Agent** | A specialized module that performs one step of the review pipeline |

## Optional LangSmith Tracing

LangGraph runs locally without an API key. To send pipeline traces to LangSmith,
copy `.env.example` to `.env` and set:

```powershell
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=icras-agent-h
```
