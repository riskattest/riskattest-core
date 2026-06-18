# `mrm-core`

> **dbt for Model Risk Management — with replay by default.**
>
> A declarative, version-controlled, plugin-extensible CLI for validating
> traditional, AI, and GenAI models against regulator-shaped standards.
> Every model invocation is captured as a tamper-evident, hash-chained,
> OTLP-exportable `DecisionRecord`.

[![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](mrm-core/LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](mrm-core/pyproject.toml)
[![Spec PRD-2](https://img.shields.io/badge/spec-PRD--2-orange.svg)](mrm-core/docs/spec/README.md)
[![Jurisdictions](https://img.shields.io/badge/jurisdictions-AU%20%E2%80%A2%20US%20%E2%80%A2%20EU%20%E2%80%A2%20CA-green.svg)](mrm-core/docs/CROSSWALK.md)

---

## Why this exists

Regulators (Fed, APRA, OSFI, EU AI Office) now expect financial
institutions to prove **how every model decision was made, on demand,
across a multi-year retention window**. Today that proof lives in
Excel, SharePoint, and screenshots stapled into GRC suites. That
shape does not survive the 2026–2027 regulatory wave:

| Date | Event |
|---|---|
| 1 Jul 2025 | APRA CPS 230 fully effective |
| 30 Apr 2026 | APRA industry-wide AI letter + CPS 230 amendments |
| 2 Aug 2026 | EU AI Act fully applicable |
| 2026–2027 | Fed SR 11-7 → **SR 26-2** transition (AI activity logging mandate) |
| 1 May 2027 | OSFI E-23 effective (expanded to all FRFIs incl. insurers) |
| 2 Aug 2027 | EU AI Act high-risk obligations (embedded systems) |

`mrm-core` is built for the world after those dates: declarative
config, version-controlled validation, immutable evidence, and 1:1
replay of every decision a model makes.

---

## The 30-second mental model

```mermaid
flowchart LR
    Y[mrm_project.yml] --> D[DAG]
    D --> R[TestRunner]
    R -- predict / generate --> Rec[(DecisionRecord<br/>hash-chained)]
    R -- test results --> Ev[(EvidencePacket<br/>hash-chained, WORM)]
    Rec -- OTLP/HTTP-JSON --> SIEM[(Bank SIEM /<br/>OTel collector)]
    Ev --> Rep[Compliance report<br/>CPS 230 / SR 11-7 / SR 26-2 /<br/>EU AI Act / OSFI E-23]
    Rep --> GRC["OpenPages / ServiceNow /<br/>Workiva (roadmap)"]
    Mon[MonitorRunner<br/>mrm monitor run] -- drift detected --> Ev
    Mon -- webhook --> WH[Slack / PagerDuty /<br/>ServiceNow]
    Mon -- metrics --> Src[MLflow / CloudWatch /<br/>Databricks / file]

    classDef store fill:#fef3c7,stroke:#d97706,color:#92400e
    classDef ext fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef monitor fill:#dcfce7,stroke:#16a34a,color:#14532d
    class Rec,Ev store
    class SIEM,GRC,WH ext
    class Mon,Src monitor
```

You write a YAML project, declare your models and their tests, point
the runner at a backend, and you get back:

1. **Evidence packets** — hash-chained, WORM-stored, regulator-shaped.
2. **Decision records** — every inference, replayable byte-for-byte.
3. **Compliance reports** — paragraph-mapped to four jurisdictions.

---

## Feature coverage

### Compliance jurisdictions (bundled, pluggable)

| Standard | Jurisdiction | Status | Source |
|---|---|---|---|
| **APRA CPS 230** | Australia | Bundled | [`builtin/cps230.py`](mrm-core/mrm/compliance/builtin/cps230.py) |
| **Federal Reserve SR 11-7** | US | Bundled | [`builtin/sr117.py`](mrm-core/mrm/compliance/builtin/sr117.py) |
| **EU AI Act Annex IV** | EU | Bundled | [`builtin/eu_ai_act.py`](mrm-core/mrm/compliance/builtin/eu_ai_act.py) |
| **OSFI E-23** | Canada | Bundled | [`builtin/osfi_e23.py`](mrm-core/mrm/compliance/builtin/osfi_e23.py) |
| **Federal Reserve SR 26-2** | US (supersedes SR 11-7 for banks >$30B) | Bundled | [`builtin/sr26_2.py`](mrm-core/mrm/compliance/builtin/sr26_2.py) |
| **Cross-standard crosswalk** | 27 concepts × 5 standards (incl. SR 11-7 → SR 26-2 transition) | Shipped | [`CROSSWALK.md`](mrm-core/docs/CROSSWALK.md) |
| **NIST AI RMF, ECB Internal Models** | crosswalk targets | Roadmap | — |

Plugins are discovered three ways — bundled, pip-installed via the
`mrm.compliance` entry-point group, or local paths declared in
`mrm_project.yml`. See [ADR-0001](mrm-core/docs/adr/0001-pluggable-compliance-standards.md).

### Validation tests (built-in, namespaced, pluggable)

| Namespace | Domain | Notes |
|---|---|---|
| `tabular.*` | Missing values, drift, leakage, calibration, discrimination, stability | Pandas-shaped data |
| `ccr.*` | Monte Carlo convergence, EPE/PFE bounds, antithetic variates, copula fit | Counterparty credit risk |
| `model.*` | Performance, bias, fairness, explainability | Cross-cutting |
| `genai.*` | Hallucination, bias, robustness, toxicity, drift, PII, latency, cost | 14 tests across 7 categories |
| `compliance.*` | Governance checks per standard | One pack per jurisdiction |

Test packs are pluggable via `@register_test`. The roadmap adds a
50+-template adversarial pack and financial-F1 entity-weighted accuracy
([P11](STRATEGY.md)).

### Continuous model monitoring

Production drift monitoring that runs in the bank's existing scheduler
(Airflow, cron, Databricks Workflows) — no daemon, no new infrastructure.
Every monitoring cycle that detects drift produces an immutable
`EvidencePacket` with compliance paragraph mapping.

| Capability | Details |
|---|---|
| **Metric sources** | `builtin` (KS / Page-Hinkley in-process), `file` (JSON / CSV), `mlflow`, `cloudwatch`, `databricks` (Lakehouse Monitoring) |
| **Drift response** | Freeze `EvidencePacket`, fire `DRIFT` trigger, send webhooks (Slack / PagerDuty / ServiceNow) |
| **Exit codes** | 0 = no drift, 1 = drift detected, 2 = error — designed for Airflow/scheduler branching |
| **Graceful degradation** | Per-metric error isolation — one CloudWatch namespace down does not blind the system |
| **Audit provenance** | Every log entry records `created_by`, `hostname`, `invocation` (scheduler type), `config_hash` (SHA-256) |
| **Bank IT hardening** | SSL-inspecting proxy support (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `HTTPS_PROXY`), `${VAR}` / `${VAR:-default}` env var expansion — no secrets in YAML |
| **Databricks integration** | Queries `_drift_metrics` Delta tables via SQL Statement Execution API; reads auth from `DATABRICKS_HOST` / `DATABRICKS_TOKEN` env vars |
| **Append-only log** | JSONL at `evidence/monitoring/{model_name}/log.jsonl` — immutable audit trail per SR 26-2 §II.AI.D |

```yaml
# models/ccr/ccr_monte_carlo.yml
monitoring:
  enabled: true
  schedule: "daily"
  metrics:
    - name: portfolio_drift
      source: builtin
      detector: ks
      reference_dataset: data/monitoring/reference_portfolio.csv
      current_dataset: data/monitoring/current_portfolio.csv
      columns: [notional, pd_annual, lgd]
      threshold: 0.05

    - name: pfe_breach_rate
      source: file
      path: data/monitoring/latest_metrics.json
      metric_key: pfe_breach_rate
      threshold: 0.10
      comparison: greater_than

    - name: lakehouse_drift
      source: databricks
      host: ${DATABRICKS_HOST}
      token: ${DATABRICKS_TOKEN}
      warehouse_id: ${SQL_WAREHOUSE_ID}
      table_name: risk_models.ccr.portfolio_data
      metric_column: ks_statistic
      filter_column: notional
      threshold: 0.05
      comparison: greater_than

  on_drift:
    revalidate: true
    freeze_evidence: true
    resolve_triggers: true

  webhooks:
    - url: https://hooks.slack.com/services/${WEBHOOK_TOKEN}
      events: [drift_detected]
      headers:
        Authorization: "Bearer ${SLACK_TOKEN}"
```

```bash
# Run monitoring for one model
mrm monitor run --models ccr_monte_carlo

# All models with monitoring.enabled: true
mrm monitor run --all

# Dry run — check metrics without triggering actions
mrm monitor run --models ccr_monte_carlo --dry-run

# View monitoring history
mrm monitor history --model ccr_monte_carlo --last 10

# Dashboard view across all models
mrm monitor status
```

Spec: [Continuous Monitoring v1](mrm-core/docs/spec/continuous-monitoring-v1.md).

### Drift detection

Pluggable detector framework — works without any optional dependency
(scipy + numpy fallbacks) and upgrades transparently when the
`frouros` extra is installed.

| Test | Detector | OSS fallback (always installed) | Optional `[drift]` upgrade |
|---|---|---|---|
| `tabular.DataDrift` | `ks` | `scipy.stats.ks_2samp` | `frouros.detectors.data_drift.KSTest` |
| `tabular.DataDrift` | `wasserstein` | `scipy.stats.wasserstein_distance` | frouros equivalent |
| `tabular.ConceptDrift` | `page_hinkley` | pure-numpy Page-Hinkley | `frouros.detectors.concept_drift.PageHinkley` |
| `genai.SemanticDrift` | `mmd` | pure-numpy MMD + RBF kernel | `frouros` MMD with frouros alerts |
| `genai.OutputConsistency` | embedding std-dev | sentence-transformers | — |

```bash
# baseline install -- drift tests still work via scipy / numpy.
pip install mrm-core

# upgrade to the higher-fidelity frouros backends:
pip install 'mrm-core[drift]'

# capability report (which backends + signers are actually available):
mrm doctor
```

### Model sources

| Source | Adapter | Replay-aware |
|---|---|---|
| Local pickle / joblib | `_load_pickle` | Yes via `instrument_predictor` |
| Python class | `_load_python_class` | Yes  |
| MLflow registry | `_load_mlflow_model` | Yes  |
| HuggingFace Hub | `_load_huggingface_model` | Yes  |
| S3 / GCS / Azure URIs | `_load_s3_model` | Yes  |
| Databricks Unity Catalog | UC backend | Yes  |
| **LLM endpoints** | LiteLLM + legacy adapters | Yes prompt + retrieval + decoding params auto-captured |

### Replay primitive

Every model invocation captures the four components required for
reconstruction:

```mermaid
flowchart TB
    subgraph DR[DecisionRecord]
        I[1 input_state<br/>features / prompt / RAG context]
        M[2 model_identity<br/>URI, version, artifact_hash,<br/>config_hash, framework, provider]
        P[3 inference_params<br/>seed, temperature, top_p,<br/>top_k, max_tokens, retrieval_k]
        O[4 output<br/>raw, pre post-processing]
    end
    DR --> H[content_hash = sha256 of canonical JSON]
    H --> C[prior_record_hash chain<br/>per-model, append-only]
    C --> B{Backend}
    B --> L[Local JSONL<br/>dev only]
    B --> S3[S3 + Object Lock<br/>COMPLIANCE mode]
    DR --> OTLP[OTLP/HTTP-JSON exporter<br/>any OTel collector]
```

| Capability | OSS | Cloud *(roadmap)* |
|---|---|---|
| Hash-chained `DecisionRecord` | Yes  | Yes |
| `@capture` decorator + `CaptureContext` | Yes  | Yes |
| Auto-capture inside `TestRunner` for **every** model archetype | Yes  | Yes |
| Local JSONL backend | Yes  | — |
| S3 + Object Lock backend | Yes  | Yes managed |
| OTLP/HTTP-JSON export | Yes  | Yes |
| `mrm replay record / reconstruct / verify / sample / verify-chain` | Yes  | Yes |
| HSM-backed signing (FIPS 140-2 L3+) | — | Yes ([P9](STRATEGY.md)) |
| Regulator-portal sample export | — | Yes  |
| 7-year retention SLA | — | Yes  |

Reference specs: [Decision Record v1](mrm-core/docs/spec/replay-record-v1.md).
ADR: [Replay as first-class](mrm-core/docs/adr/0003-replay-as-first-class-primitive.md).

### Evidence vault

| Capability | OSS | Cloud *(roadmap)* |
|---|---|---|
| Hash-chained `EvidencePacket` | Yes  | Yes |
| Local + S3 Object Lock backends | Yes  | Yes managed |
| HMAC-chained fast-path event log (per-session keys, daily rotation) | Yes | Yes |
| RFC-6962 Merkle daily root | Yes | Yes |
| `LocalSigner` (HMAC) + `GpgSigner` + `AgeSigner` | Yes | — |
| `KmsSigner` -- AWS KMS today; GCP / Azure pluggable | Yes | Yes managed |
| HSM-backed root signing (FIPS 140-2 L3+, AWS CloudHSM / GCP Cloud HSM / Azure Dedicated HSM) | Plug-point only | Yes (paid) |
| Conformance test-vector suite (3 positive + 3 negative) | Yes | — |
| 7-year retention SLA | — | Yes |
| Customer-managed keys (BYOK) | — | Yes Enterprise |

Spec: [Evidence Vault v1](mrm-core/docs/spec/evidence-vault-v1.md).
ADRs: [Content-addressed hash chains](mrm-core/docs/adr/0002-evidence-vault-hash-chain.md), [Signer plug-point and HSM tier line](mrm-core/docs/adr/0006-signer-plugpoint-and-hsm-tiering.md).

### dbt-style workflow primitives

| Feature | Shape |
|---|---|
| **DAG** | `depends_on:` in YAML, topological sort, parallel execution |
| **`ref()`** | Reference other models by name |
| **Graph operators** | `+model`, `model+`, `+model+` |
| **`--select`** | `mrm test --select tier:tier_1 --select tag:credit` |
| **`mrm docs generate`** | Compile compliance reports, like `dbt docs generate` |
| **Validation triggers** | scheduled, drift, breach, materiality, regulatory, manual |

---

## What it looks like

A model declared in YAML:

```yaml
models:
  - name: ccr_monte_carlo
    version: 1.4.0
    tier: tier_1
    location:
      type: file
      path: artifacts/ccr_v140.pkl
    depends_on:
      - market_data_curve
    tests:
      - test: ccr.MCConvergence
        params: { paths: 100000, tolerance: 1e-3 }
      - test: ccr.EPEBounds
        compliance:
          cps230: ["27", "28"]
          sr117:  ["III.A", "V.B"]
          eu_ai_act: ["Annex IV §2.b"]
```

One CLI invocation compiles the whole thing into evidence + reports +
replay:

```bash
mrm test --select ccr_monte_carlo
mrm docs generate ccr_monte_carlo --compliance standard:cps230,sr117,eu_ai_act
mrm evidence freeze ccr_monte_carlo                  # backend resolved from profile
mrm replay sample --model ccr_monte_carlo --since 2026-01-01 --n 50
```

No `--backend`, `--bucket`, or `--retention` flag in normal operation —
the active profile decides, so the same command works in dev (local
filesystem) and prod (S3 + Object Lock) without code changes.

---

## Configuration — dbt-style project / profile split

Every configurable backend in `mrm-core` is resolved through one
ladder, modelled on `dbt-core` / `dbt Cloud`:

```
1. CLI flag passed on this invocation
2. env var (e.g. MRM_BACKEND_DEFAULT_EVIDENCE_BUCKET=...)
3. profiles.yml: mrm.outputs.<target>.<section>.<role>
4. mrm_project.yml: <section>.<role>
5. hard-coded default
```

**`mrm_project.yml`** declares what the project IS — the role names
and their capability-shaped defaults, committed to git:

```yaml
name: ccr_example
version: 1.0.0

backends:
  default_results:   { type: local }
  default_evidence:  { type: local, retention_days: 2555 }
  default_replay:    { type: local }
  root_signer:       { type: local }     # local | gpg | age | kms | cloud-hsm

catalogs:
  databricks:
    type: databricks_unity
    mlflow_registry: true
```

**`profiles.yml`** declares where the project RUNS — host names,
bucket names, credentials, per target. Typically lives outside the
repo (secrets manager, `~/.mrm/profiles.yml`, or per-env injection):

```yaml
mrm:
  target: dev
  outputs:
    dev:
      backends:
        default_evidence: { type: local }
        default_replay:   { type: local }
      catalogs:
        databricks:
          host: "{{ env_var('DATABRICKS_HOST_DEV') }}"
          token: "{{ env_var('DATABRICKS_TOKEN_DEV') }}"
          catalog: workspace_dev
          schema: sandbox
          cache_ttl_seconds: 60

    prod:
      backends:
        default_evidence:
          type: s3_object_lock
          bucket: bank-evidence-prod
          region: ap-southeast-2
        default_replay:
          type: s3
          bucket: bank-replay-prod
        root_signer:
          type: kms
          key_uri: aws-kms://ap-southeast-2/alias/mrm-root-prod
      catalogs:
        databricks:
          host: "{{ env_var('DATABRICKS_HOST_PROD') }}"
          token: "{{ env_var('DATABRICKS_TOKEN_PROD') }}"
          catalog: workspace_prod
          schema: gold
          cache_ttl_seconds: 600
```

### Backend roles

| Role | Purpose | OSS types | Paid-tier types |
|---|---|---|---|
| `default_results` | Test-results storage | `local`, `mlflow` | — |
| `default_evidence` | Evidence vault | `local`, `s3_object_lock` | — (managed via Cloud) |
| `default_replay` | DecisionRecord chain store | `local`, `s3` | — (managed via Cloud) |
| `root_signer` | Daily Merkle root signature | `local`, `gpg`, `age`, `kms` | `cloud-hsm` (FIPS 140-2 L3+) |

### Catalogs

Catalogs are **declared** in `mrm_project.yml` (existence + capability
flags) and **bound** in `profiles.yml` (host / token / catalog /
schema / cache TTL). A catalog declared but not bound for the active
target raises a clear error — no silent no-op loads.

### Switching targets

```bash
mrm test --select ccr_monte_carlo                 # uses default target (dev)
mrm test --select ccr_monte_carlo --profile prod  # switches to prod bindings
mrm evidence freeze ccr_monte_carlo --profile prod
```

### Overriding for one invocation

CLI flags survive as last-write-wins overrides:

```bash
# Resolution falls through the chain.
mrm evidence freeze ccr_monte_carlo

# Override just the bucket for this run.
mrm evidence freeze ccr_monte_carlo --bucket bank-evidence-test

# Override via env var (highest precedence below CLI).
MRM_BACKEND_DEFAULT_EVIDENCE_BUCKET=ad-hoc \
  mrm evidence freeze ccr_monte_carlo
```

### When something is missing

The resolver raises a diagnostic naming every layer it searched:

```
Required backends role 'default_evidence' could not be resolved for target 'staging'.
  Searched (highest to lowest precedence):
    1. CLI flag overrides (none, or all None)
    2. Env vars matching MRM_BACKEND_DEFAULT_EVIDENCE_<KEY>
    3. profiles.yml: mrm.outputs.staging.backends.default_evidence
    4. mrm_project.yml: backends.default_evidence
  Add the role to at least one layer.
```

### Secrets handling

- Literal credentials never appear in either YAML.
- Two indirection forms are supported:
  - **Jinja-style** — `host: "{{ env_var('DATABRICKS_HOST_PROD') }}"`.
  - **Suffix sugar** — `bucket_env: MY_BUCKET_NAME`.
- Missing env vars render to `null`; the resolver is silent at parse,
  loud at use, matching the dbt convention.

See [`mrm/core/backend_resolver.py`](mrm-core/mrm/core/backend_resolver.py)
for the reference implementation and
[`ccr_example/`](mrm-core/ccr_example/) /
[`credit_risk_example/`](mrm-core/credit_risk_example/) for worked
configs.

---

## CLI surface at a glance

```
mrm init <project>                   # scaffold new project
mrm list  models|tests|suites        # introspect
mrm test  [--select ...] [--threads N]
mrm docs  generate|list-standards|crosswalk
mrm evidence  freeze|verify|list
mrm evidence root  publish|verify|show|list-signers
mrm evidence conformance  run
mrm replay  record|reconstruct|verify|sample|verify-chain
mrm triggers  check|list|run
mrm monitor  run|history|status         # continuous drift monitoring
mrm catalog  list|publish|sync          # Databricks UC + MLflow
mrm doctor                              # capability report: drift backends + signers
mrm debug  --show-config|--show-dag|--show-catalog
```

Every command accepts `--profile <target>` to switch profile targets
(default `dev`). Backend-shaped flags (`--backend`, `--bucket`,
`--retention`, `--signer`, `--key-path`) default to `None` and only
override the project/profile chain when explicitly passed.

Full help: `mrm <command> --help`.

---

## Architectural plug points

```mermaid
flowchart TB
    subgraph Core[mrm-core]
        CLI[Typer CLI]
        Proj[Project + YAML config]
        DAG[DAG / catalog / triggers]
        Eng[TestRunner]
        Mon[MonitorRunner<br/>continuous drift monitoring]
        Repl[replay/]
        Ev[evidence/]
    end

    subgraph Plugins[Pluggable extensions]
        ST["@register_standard<br/>compliance plugins"]
        TS["@register_test<br/>test plugins"]
        MtS["@_register_source<br/>metric source adapters"]
        EB[EvidenceBackend ABC<br/>WORM substrates]
        RB[ReplayBackend ABC<br/>chain stores]
        MS[Model sources<br/>pickle / MLflow / HF / UC / LLM]
    end

    subgraph External[External enterprise systems]
        MLOps["MLflow / UC / SageMaker /<br/>Vertex / DVC / W&amp;B"]
        WORM[S3 Object Lock /<br/>Azure Immutable Blob /<br/>UC + audit log]
        SIEM[OTel collector / Splunk /<br/>Datadog / Grafana]
        GRCx["OpenPages / ServiceNow /<br/>Workiva (roadmap)"]
    end

    CLI --> Eng
    CLI --> Mon
    Proj --> DAG --> Eng
    Eng -.calls.-> MS
    Eng -.emits.-> Repl
    Eng -.emits.-> Ev
    Mon -.collects.-> MtS
    Mon -.emits.-> Ev
    ST --> Eng
    TS --> Eng
    MtS --> MLOps
    Repl --> RB --> WORM
    Repl --> SIEM
    Ev --> EB --> WORM
    Ev --> GRCx
    MS --> MLOps

    classDef plug fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e
    classDef ext fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    class ST,TS,MtS,EB,RB,MS plug
    class MLOps,WORM,SIEM,GRCx ext
```

Six plug points, all behind small, versioned contracts. The
contracts are documented in [`docs/spec/`](mrm-core/docs/spec/).

---

## Quick start

```bash
# install
git clone https://github.com/dbose/mrm.git
cd mrm/mrm-core
pip install -e .

# run the canonical CCR Monte Carlo example end-to-end
cd ccr_example
python setup_ccr_example.py     # synthetic data + pickled model
python run_validation.py        # 8 tests + triggers + report

# set up continuous monitoring
python setup_monitoring.py      # generate reference + current datasets
mrm monitor run --models ccr_monte_carlo

# or via the CLI
mrm docs generate ccr_monte_carlo --compliance standard:cps230
mrm replay record   ccr_monte_carlo --inputs trade_book.csv
mrm replay verify   <record-id> --tolerance 1e-6
mrm replay sample   --model ccr_monte_carlo --n 10
mrm evidence freeze ccr_monte_carlo --backend local
```

A worked GenAI example is under [`genai_example/`](mrm-core/genai_example/)
— a RAG customer-service assistant validated end-to-end against CPS
230 and EU AI Act mappings.

---

## Worked examples

| Example | Domain | Models | Standards exercised |
|---|---|---|---|
| [`ccr_example/`](mrm-core/ccr_example/) | Counterparty credit risk | Monte Carlo simulation | CPS 230, SR 11-7, EU AI Act |
| [`credit_risk_example/`](mrm-core/credit_risk_example/) | PD / LGD scoring | scikit-learn classifier | CPS 230, SR 11-7 |
| [`genai_example/`](mrm-core/genai_example/) | RAG customer service | LiteLLM + FAISS | CPS 230, EU AI Act |

The CCR example is the de-facto integration test for the framework as
a whole. If a change breaks `python run_validation.py`, the change is
broken.

XVA via ORE and IRB credit-risk examples are next ([P13](STRATEGY.md), [P14](STRATEGY.md)).

---

## How `mrm-core` integrates, doesn't replace

```mermaid
flowchart LR
    subgraph MLOps[MLOps stack — read from]
        MLflow
        UC[Unity Catalog]
        SM[SageMaker MR]
        VA[Vertex AI]
        DVC
        WB["W&amp;B"]
    end
    subgraph mrm[mrm-core]
        m[CLI + DAG + tests + replay + evidence]
    end
    subgraph WORM[Immutable storage — write to]
        S3OL[S3 Object Lock]
        AIB[Azure Immutable Blob]
        UCG[UC + audit log]
    end
    subgraph GRC[GRC platforms — push to]
        OP[OpenPages]
        SN[ServiceNow IRM]
        WV[Workiva]
    end
    MLOps --> mrm --> WORM
    mrm --> GRC
```

`mrm-core` is the **glue between MLOps and GRC**, not a replacement
for either.

---

## Spec and governance posture

Production-grade open-source MRM is unusual; we treat the public
contracts as formal specs from day one.

- [`GOVERNANCE.md`](mrm-core/GOVERNANCE.md) — maintainer model, spec
  lifecycle, intent to transition to a neutral foundation
  (OpenSSF / CNCF / FINOS) once adoption thresholds are met.
- [`docs/adr/`](mrm-core/docs/adr/) — Architecture Decision Records
  for every load-bearing design choice (6 ADRs and counting).
- [`docs/spec/`](mrm-core/docs/spec/) — PRD-2 specs for:
  - [Decision Record v1](mrm-core/docs/spec/replay-record-v1.md)
  - [Evidence Vault Chain v1](mrm-core/docs/spec/evidence-vault-v1.md)
  - [Compliance Plugin Contract v1](mrm-core/docs/spec/compliance-plugin-v1.md)
- [`STRATEGY.md`](STRATEGY.md) — public roadmap with each feature's
  status, OSS / Cloud tier, and wedge thesis.

---

## Project layout

```
mrm-core/
├── mrm/
│   ├── cli/                       Typer CLI
│   ├── core/                      Project loading, DAG, catalog, triggers
│   │   ├── backend_resolver.py    Unified config resolution (dbt-style)
│   │   └── catalog_backends/      Databricks UC + MLflow integration
│   ├── compliance/                Pluggable regulatory standards
│   │   └── builtin/               cps230 · sr117 · sr26_2 · euaiact · osfie23
│   ├── tests/                     Pluggable test framework
│   │   └── builtin/               tabular · ccr · model · genai
│   ├── engine/runner.py           Test runner with replay wiring
│   ├── evidence/                  Hash-chained evidence vault
│   │   └── backends/              local · s3_object_lock
│   ├── monitor/                   Continuous drift monitoring
│   │   ├── config.py              YAML config parsing + ${VAR} expansion
│   │   ├── metrics.py             Source adapters (builtin · file · mlflow · cloudwatch · databricks)
│   │   ├── runner.py              Orchestrator: collect → evaluate → evidence → webhook
│   │   ├── log.py                 Append-only JSONL audit log
│   │   └── webhook.py             Webhook sender with proxy/TLS support
│   ├── replay/                    1:1 Decision Replay
│   │   ├── record.py              DecisionRecord schema
│   │   ├── capture.py             @capture decorator + context manager
│   │   ├── instrument.py          Universal predictor + LLM capture
│   │   ├── otlp.py                OTLP/HTTP-JSON exporter
│   │   ├── verify.py              reconstruct + diff
│   │   └── backends/              local · s3
│   └── backends/                  Storage backends + LLM adapters
├── docs/
│   ├── adr/                       Architecture Decision Records
│   ├── spec/                      Normative versioned specs (PRD-2)
│   ├── guides/                    User-facing walkthroughs
│   └── CROSSWALK.md               Cross-standard mapping
├── ccr_example/                   Canonical CCR Monte Carlo example
├── credit_risk_example/           Credit risk PD example
└── genai_example/                 RAG customer-service example
```

---

## Status

**Done (shipped):**

- CLI with dbt-style ergonomics
- DAG, `ref()`, graph operators, topological sort, parallel execution
- Built-in tests across 4 namespaces (tabular · ccr · model · genai)
- Four bundled jurisdictions (AU / US / EU / CA), plus **Fed SR 26-2** (AI-specific successor to SR 11-7)
- Cross-standard crosswalk (27 concepts × 5 standards, with explicit SR 11-7 → SR 26-2 transition map)
- Validation trigger engine (6 trigger types)
- Databricks UC + MLflow + HuggingFace integration
- **Unified backend / catalog config resolver — dbt-style `mrm_project.yml` (declarative) + `profiles.yml` (per-target bindings); precedence ladder CLI > env > profile > project > defaults**
- Evidence vault — hash-chained packets, S3 Object Lock backend
- **Cryptographic vault hardening — HMAC-chained event log, RFC-6962 Merkle daily root, pluggable Signer (`local` / `gpg` / `age` / `kms` OSS; `cloud-hsm` paid-tier stub), 6 conformance vectors**
- **Drift detection — pluggable detector registry (KS / Wasserstein / Page-Hinkley / MMD) with scipy/numpy fallbacks always installed; opt-in frouros backend via `pip install 'mrm-core[drift]'`; `tabular.DataDrift` / `tabular.ConceptDrift` / `genai.SemanticDrift` tests routed through the registry; `mrm doctor` capability report**
- GenAI test pack — 14 tests, LiteLLM unified interface, RAG validation
- **1:1 Decision Replay — DecisionRecord, capture, OTLP, verify, backends, CLI**
- Replay capture for **all** model types — sklearn, HF, MLflow, LiteLLM, legacy LLM adapters
- **Continuous model monitoring — 5 metric source adapters (builtin / file / mlflow / cloudwatch / databricks), drift-triggered re-validation with EvidencePacket freeze + DRIFT triggers + webhook notifications, append-only audit log with provenance, bank IT hardening (proxy/TLS, `${VAR}` env expansion), graceful degradation, Databricks Lakehouse Monitoring integration via SQL Statement Execution API**
- ADRs + spec PRDs + GOVERNANCE.md posture

**Test coverage:** 256 pytest passing + 59 end-to-end acceptance
checks against the worked examples.

**Next:** 50+-template adversarial pack, GRC connectors (OpenPages,
ServiceNow, Workiva), XVA worked example via ORE. See
[STRATEGY.md](STRATEGY.md).

---

## Contributing

PRs welcome. Read [CONTRIBUTING.md](mrm-core/CONTRIBUTING.md) and
[GOVERNANCE.md](mrm-core/GOVERNANCE.md) first. Non-trivial
architectural changes need an [ADR](mrm-core/docs/adr/template.md).

## License

Apache 2.0 — see [LICENSE](mrm-core/LICENSE).
