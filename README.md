# ICRAS — Intelligent Contract Review and Approval System

ICRAS is a multi-agent AI pipeline that automates the review, risk assessment, and approval of legal contracts. It ingests a **contract bundle** (PDF contract + supporting policy files), runs it through a chain of specialized agents, and produces a structured **approval packet** with findings, risk scores, and recommendations.

> **Sprint 1 Status:** Foundation only. Repository structure, Pydantic schemas, bundle loading, and deterministic run folders are implemented. Actual agent intelligence (LLM calls) will be added in later user stories.

---

## Folder Structure

```
icras/
├── main.py                  # CLI entry point
├── agents/                  # Agent placeholders (no LLM logic yet)
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

Edit `.env` and fill in your API keys when needed (not required for Sprint 1).

---

## Running the Pipeline

### Run with the Clean NDA sample bundle

```powershell
python main.py --bundle data/bundles/clean_nda
```

### Run with the Services Agreement sample bundle

```powershell
python main.py --bundle data/bundles/services_agreement
```

Each run creates a unique folder under `runs/` with this structure:

```
runs/<run_id>/
├── metadata.json       # Run ID, bundle path, timestamps, status
├── config.json         # Initial pipeline configuration
└── audit_log.jsonl     # Audit trail (initially empty)
```

Running the same bundle multiple times produces separate run folders.

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
`tests/test_policy_rules.py::test_policy_yaml_edit_changes_payment_terms_decision`
demonstrates the visible decision change: a net-60 payment clause is flagged
under net-30 policy and accepted after the YAML is changed to net-60.

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Bundle** | A folder containing a contract PDF and supporting YAML/CSV policy files |
| **Run** | A single pipeline execution, identified by a unique `run_id` |
| **Schema** | A Pydantic v2 model that validates structured data between agents |
| **Agent** | A specialized module that performs one step of the review pipeline |

