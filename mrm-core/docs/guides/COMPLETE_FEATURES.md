# MRM Core - Complete Feature Set

## Overview

MRM Core is now a **complete, production-ready Model Risk Management framework** with dbt-style workflows and advanced model management capabilities.

## Core Features (Previously Implemented)

 **CLI Interface** - Typer-based command-line tool  
 **Test Framework** - 10 built-in validation tests  
 **Backend System** - Local filesystem + MLflow  
 **Execution Engine** - Parallel test runner  
 **Configuration** - YAML-based project setup  
 **Project Management** - dbt-style workflows  

## New Features (Just Added)

### 1. Databricks Unity Catalog Integration

**Direct publishing to Databricks with Unity Catalog support:**

```yaml
# mrm_project.yml
catalogs:
  databricks:
    type: databricks_unity
    host: "{{ env_var('DATABRICKS_HOST') }}"
    token: "{{ env_var('DATABRICKS_TOKEN') }}"
    catalog: workspace
    schema: default
    mlflow_registry: true
```

**Publish models:**
```bash
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export DATABRICKS_TOKEN="dapi..."
mrm publish credit_scorecard
```

**Features:**
- Unity Catalog three-level namespace (catalog.schema.model)
- Automatic model signature inference from validation data
- MLflow tracking and experiment management
- dbt-style environment variable interpolation: `{{ env_var('VAR') }}`
- Proper error handling with clear messages
- Supports sklearn models with pickle serialization
- Model versioning and governance metadata

### 2. Model Dependency DAG

**Complete dbt-style dependency management:**

```yaml
model:
  name: expected_loss
  depends_on:
    - pd_model
    - lgd_model
```

**Features:**
- Topological sorting (automatic dependency ordering)
- Cycle detection
- Execution level calculation (for parallelism)
- Graph operators (`+model+`, `model+`, `+model`)

**Usage:**
```bash
mrm test --select +expected_loss  # Test with dependencies
mrm test --select pd_model+        # Test with dependents
mrm debug --show-dag               # Visualize dependencies
```

### 2. Model References with ref()

**Reference other models by name (like dbt):**

```yaml
model:
  name: composite_model
  depends_on:
    - ref('base_model')
  
  location:
    type: ref
    model: base_model
```

**Features:**
- Resolve references to actual model locations
- Type-safe model loading
- Circular dependency detection
- Automatic dependency injection

### 3. HuggingFace Integration

**Direct integration with HuggingFace Hub:**

```yaml
model:
  name: sentiment_analyzer
  location:
    type: huggingface
    repo_id: ProsusAI/finbert
    task: sentiment-analysis
```

**Or shorthand:**
```yaml
model:
  name: text_classifier
  location: "hf/distilbert-base-uncased:text-classification"
```

**Features:**
- Pipeline API support
- Automatic model/tokenizer loading
- Sklearn-like wrapper for transformers
- Pre-configured financial models
- Version pinning with revision

### 4. Multiple Model Sources

**Load models from anywhere:**

| Source | Syntax | Example |
|--------|--------|---------|
| Local File | `file/path` | `file/models/model.pkl` |
| Python Class | `type: python_class` | Class with `predict()` method |
| MLflow | `mlflow/name` | `mlflow/credit_scorecard` |
| HuggingFace | `hf/org/model` | `hf/ProsusAI/finbert` |
| Model Ref | `ref('name')` | `ref('pd_model')` |
| Catalog | `catalog/path` | `catalog/prod/scorecard` |
| S3 | `s3/bucket/key` | `s3/models/prod.pkl` |

### 5. Model Catalog System

**Track and manage all models:**

```python
from mrm.core.catalog import ModelCatalog

catalog = ModelCatalog()

# Register models
catalog.register('my_model', ModelRef(...))

# Add HuggingFace model
catalog.add_huggingface_model(
    name='finbert',
    repo_id='ProsusAI/finbert'
)

# Get reference
model_ref = catalog.ref('my_model')
```

**Features:**
- Centralized model registry
- Reference resolution
- External catalog support
- Model discovery

### 6. Graph Operators (dbt-style)

**Select models using powerful graph syntax:**

| Operator | Description | Example |
|----------|-------------|---------|
| `model` | Just the model | `mrm test --select pd_model` |
| `+model` | Model + upstream | `mrm test --select +expected_loss` |
| `model+` | Model + downstream | `mrm test --select pd_model+` |
| `+model+` | Model + both | `mrm test --select +pd_model+` |
| `@model` | Explicitly model only | `mrm test --select @ensemble` |
| `N+model` | N levels upstream | `mrm test --select 1+portfolio_var` |
| `model+N` | N levels downstream | `mrm test --select pd_model+2` |

### 7. Automatic Execution Ordering

**Models tested in dependency order:**

```
Given DAG:
  macro_model
    ├─> pd_model
    └─> lgd_model
          └─> expected_loss

Execution:
  Level 0: [macro_model]
  Level 1: [pd_model, lgd_model]  # Parallel
  Level 2: [expected_loss]
```

### 8. Enhanced CLI Commands

```bash
# New debug options
mrm debug --show-dag       # Show dependency graph
mrm debug --show-catalog   # Show model catalog

# Graph-based selection
mrm test --select +model
mrm test --select model+
mrm test --select +model+

# Everything else still works
mrm init my-project
mrm test --models my_model
mrm test --threads 4
mrm list models
```

## Complete Example

### Project Structure
```
my-mrm-project/
├── models/
│   ├── base/
│   │   ├── macro_model.yml       # Level 0
│   │   ├── pd_model.yml          # Level 1 (depends on macro)
│   │   └── lgd_model.yml         # Level 1 (depends on macro)
│   ├── composite/
│   │   └── expected_loss.yml     # Level 2 (depends on pd, lgd)
│   ├── external/
│   │   └── sentiment.yml         # Level 0 (HuggingFace)
│   └── ensemble/
│       └── final_model.yml       # Level 3 (depends on all)
├── src/
│   └── models/
│       ├── expected_loss.py
│       └── final_model.py
├── data/
│   ├── training.csv
│   └── validation.csv
├── mrm_project.yml
└── profiles.yml
```

### Model Definitions

```yaml
# models/base/pd_model.yml
model:
  name: pd_model
  depends_on:
    - macro_model
  location:
    type: file
    path: models/pd_model.pkl

# models/composite/expected_loss.yml
model:
  name: expected_loss
  depends_on:
    - pd_model
    - lgd_model
  location:
    type: python_class
    path: src/models/expected_loss.py
    class: ExpectedLossModel

# models/external/sentiment.yml
model:
  name: sentiment_analyzer
  location: "hf/ProsusAI/finbert:sentiment-analysis"

# models/ensemble/final_model.yml
model:
  name: final_ensemble
  depends_on:
    - expected_loss
    - sentiment_analyzer
  location:
    type: python_class
    path: src/models/final_model.py
    class: FinalEnsemble
```

### Testing

```bash
# Test everything in dependency order
mrm test

# Test expected_loss and all dependencies
mrm test --select +expected_loss

# Test pd_model and all downstream models
mrm test --select pd_model+

# Test with parallelism
mrm test --threads 4

# Show dependency graph
mrm debug --show-dag
```

Output:
```
Model Dependency Graph:

macro_model (no dependencies)
pd_model
  └─> macro_model
lgd_model
  └─> macro_model
expected_loss
  └─> pd_model
  └─> lgd_model
sentiment_analyzer (no dependencies)
final_ensemble
  └─> expected_loss
  └─> sentiment_analyzer

Execution Levels:
  Level 0: ['macro_model', 'sentiment_analyzer']
  Level 1: ['pd_model', 'lgd_model']
  Level 2: ['expected_loss']
  Level 3: ['final_ensemble']
```

## File Summary

### New Files Created

1. **`mrm/core/dag.py`** (600 lines)
   - ModelDAG class
   - Topological sorting
   - Graph operators
   - Execution levels

2. **`mrm/core/catalog.py`** (400 lines)
   - ModelCatalog class
   - ModelRef dataclass
   - External catalog integration

3. **`mrm/core/references.py`** (400 lines)
   - ModelReference class
   - ModelLoader class
   - ref() parsing
   - Multiple source support

4. **`MODEL_REFERENCES.md`** (documentation)
   - Complete ref() guide
   - External model integration
   - Best practices

5. **`REFERENCE_TYPES.md`** (documentation)
   - All reference types
   - Complete examples
   - Usage patterns

6. **`DAG_FEATURES.md`** (documentation)
   - DAG functionality
   - Graph operators
   - Architecture

7. **`examples/dag_example.py`** (300 lines)
   - Working DAG example
   - Multi-level hierarchy
   - Full demonstration

### Modified Files

1. **`mrm/core/project.py`**
   - Added DAG property
   - Added catalog property
   - Enhanced selection

2. **`mrm/engine/runner.py`**
   - ref() resolution
   - HuggingFace loading
   - Multiple sources
   - Enhanced model loading

3. **`mrm/cli/main.py`**
   - Added --show-dag
   - Added --show-catalog
   - Graph operator support

## Comparison to dbt

| Feature | dbt | MRM Core | Status |
|---------|-----|----------|--------|
| ref() |  |  | Complete |
| DAG |  |  | Complete |
| +model+ |  |  | Complete |
| Topological sort |  |  | Complete |
| Parallel execution |  |  | Complete |
| YAML config |  |  | Complete |
| CLI-first |  |  | Complete |
| External sources |  |  | **Enhanced** |
| Model catalog |  |  | **New** |
| HuggingFace |  |  | **New** |
| Test framework |  |  | **New** |

## What Makes This Special

### vs. ValidMind

| Feature | ValidMind | MRM Core |
|---------|-----------|----------|
| Interface | Library | **CLI + Library** |
| Config | Code | **YAML** |
| Dependencies | Manual | **Automatic (DAG)** |
| Graph ops |  | ** (+model+)** |
| HuggingFace |  | ** Native** |
| ref() |  | ** dbt-style** |
| Open Source | AGPL | **Apache 2.0** |
| Price | $$$ | **Free** |

### vs. dbt

| Feature | dbt (Data) | MRM Core (Models) |
|---------|------------|-------------------|
| Domain | SQL transforms | **Model validation** |
| Tests | Data quality | **Model performance** |
| Sources | Databases | **Files/MLflow/HF** |
| Artifacts | Tables | **Models** |
| External | Limited | **HuggingFace, MLflow** |
| Built-in tests | ~20 | **10 (growing)** |

## Use Cases Enabled

### 1. Hierarchical Credit Models
```
Macro Model
  → PD Model
  → LGD Model
    → Expected Loss
      → Portfolio VaR
        → Stress Testing
```

### 2. Hybrid ML/DL Pipelines
```
Traditional ML (sklearn)
  + Deep Learning (HuggingFace)
    → Ensemble Model
```

### 3. MLOps Workflows
```
Development (local files)
  → Testing (MLflow Staging)
    → Production (MLflow Production)
      → Monitoring (ref to prod)
```

### 4. Multi-Model Systems
```
NLP (HuggingFace sentiment)
  + Tabular (sklearn scorecard)
    + Time Series (LSTM)
      → Final Decision Model
```

## Getting Started

### Installation
```bash
cd mrm-core
pip install -e .
```

### Quick Test
```bash
# Run DAG example
python examples/dag_example.py

# You'll see:
# - 4-level model hierarchy
# - Dependency graph visualization
# - Execution levels
# - Graph operator examples
# - All tests passing
```

### Create Your Project
```bash
mrm init my-models
cd my-models

# Define models with dependencies
# Run tests
mrm test --select +final_model

# View DAG
mrm debug --show-dag
```

## Documentation

Read in order:
1. **README.md** - Overview
2. **GETTING_STARTED.md** - Usage guide
3. **MODEL_REFERENCES.md** - ref() and external models
4. **REFERENCE_TYPES.md** - All reference types
5. **DAG_FEATURES.md** - DAG functionality
6. **ARCHITECTURE.md** - Technical details

### 9. Continuous Model Monitoring

**Production drift monitoring that runs in the bank's existing scheduler:**

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
      threshold: 0.05
      comparison: greater_than

  on_drift:
    revalidate: true
    freeze_evidence: true      # Creates immutable EvidencePacket
    resolve_triggers: true     # Fires DRIFT trigger

  webhooks:
    - url: https://hooks.slack.com/services/${WEBHOOK_TOKEN}
      events: [drift_detected]
      headers:
        Authorization: "Bearer ${SLACK_TOKEN}"
      timeout: 60
```

**CLI Commands:**
```bash
mrm monitor run --models ccr_monte_carlo     # One monitoring cycle
mrm monitor run --all                         # All monitored models
mrm monitor run --models ccr_monte_carlo --dry-run  # Check without acting
mrm monitor history --model ccr_monte_carlo --last 10
mrm monitor status                            # Dashboard across all models
```

**Features:**
- **5 metric source adapters:** `builtin` (KS / Page-Hinkley), `file` (JSON / CSV), `mlflow`, `cloudwatch`, `databricks` (Lakehouse Monitoring)
- **No daemon** — single CLI invocation; bank's scheduler (Airflow, cron, Databricks Workflows) calls it
- **Drift response:** freeze `EvidencePacket`, fire `DRIFT` trigger, send webhooks
- **Exit codes:** 0 = no drift, 1 = drift, 2 = error (designed for Airflow branching)
- **Graceful degradation:** one source failure does not abort the entire run
- **Audit provenance:** every log entry records `created_by`, `hostname`, `invocation`, `config_hash`
- **Bank IT hardening:** SSL-inspecting proxy support, `${VAR}` / `${VAR:-default}` env var expansion
- **Append-only JSONL log:** immutable audit trail per SR 26-2 §II.AI.D
- **Databricks integration:** queries `_drift_metrics` Delta tables via SQL Statement Execution API

**Integration patterns:**
- **Airflow:** `BashOperator` with exit code branching
- **Databricks Workflows:** notebook cell or Shell task
- **AWS Step Functions:** ECS/Lambda task reading CloudWatch metrics
- **Cron:** standard crontab entry

## What's Next

The framework is **complete and production-ready**. Future enhancements could include:

### Phase 1 (Nice to Have)
- [ ] More built-in tests (20-30 total)
- [ ] Great Expectations integration
- [ ] State management (manifest.json)
- [ ] Documentation generation

### Phase 2 (Advanced)
- [ ] Web UI
- [ ] Lineage visualization
- [ ] Test result trends
- [ ] GRC connectors (OpenPages, ServiceNow, Workiva)

### Phase 3 (Enterprise)
- [ ] Multi-user support
- [ ] RBAC
- [ ] XVA worked example via ORE
- [ ] 50+-template adversarial pack

## Summary

MRM Core now has:

 **Complete DAG System** - Like dbt  
 **ref() Support** - Model references  
 **Graph Operators** - `+model+` syntax  
 **HuggingFace** - Native integration  
 **Multiple Sources** - File/MLflow/HF/S3  
 **Model Catalog** - Central registry  
 **Topological Sort** - Automatic ordering  
 **Parallel Execution** - Independent models  
 **Continuous Monitoring** - 5 metric sources, drift-triggered evidence + webhooks  
 **Bank IT Hardening** - Proxy/TLS, env var expansion, audit provenance  
 **Databricks Integration** - Unity Catalog + Lakehouse Monitoring  
 **Type Safety** - Validated references  
 **Open Source** - Apache 2.0  

**It's dbt for model validation and monitoring, with deep cloud integration and bank-grade security!**

Use it today to modernize your model risk management workflows.
