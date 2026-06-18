# Roadmap

`mrm-core` is being developed openly. The roadmap below is non-binding
— priorities shift as the regulatory landscape evolves and as the
community contributes.

## Shipped

- CLI with dbt-style ergonomics + DAG + pluggable test framework
- 5 bundled compliance standards: APRA CPS 230 · Fed SR 11-7 ·
  Fed SR 26-2 · EU AI Act Annex IV · OSFI E-23
- Cross-standard crosswalk (27 concepts × 5 standards) including the
  SR 11-7 → SR 26-2 transition map
- Validation triggers (6 types: scheduled / drift / breach /
  materiality / regulatory / manual)
- Evidence vault — hash-chained packets + local + S3 Object Lock
  backends; SEC 17a-4-shape immutability
- Cryptographic vault hardening — HMAC-chained event log, RFC-6962
  Merkle daily root, pluggable signer (Local / GPG / age / AWS KMS),
  conformance test-vector suite
- 1:1 Decision Replay — DecisionRecord schema, capture decorator,
  OTLP exporter, replay verification
- Replay capture for every model archetype — sklearn, HF, MLflow,
  LiteLLM and the legacy LLM adapters
- GenAI test pack (14 tests across 7 categories) + LiteLLM unified
  endpoint adapter
- Drift detection — pluggable detector framework (KS, Wasserstein,
  Page-Hinkley, MMD) with scipy/numpy fallbacks; optional frouros
  backend via `pip install 'mrm-core[drift]'`
- dbt-style project/profile config split + unified resolver
- Specs PRD-2: Decision Record (v1), Evidence Vault Chain (v1),
  Compliance Plugin Contract (v1)
- Architecture Decision Records (6) + GOVERNANCE.md + MAINTAINERS.md
- Test coverage: 158 pytest + 59 end-to-end acceptance checks

## In progress

- LLM adversarial red-team pack — 50+ attack templates across
  fiduciary-bypass / PII-extraction / jailbreak / system-prompt
  override / regulatory-claim fabrication; financial-F1 entity-weighted
  accuracy
- ADR + spec governance posture extensions for community contributions

## Feature Backlog
- Feature 1: Regulator portal — the single highest-leverage addition
What no competitor has: a read-only, time-bounded evidence portal that a bank can hand to APRA, the Fed, or the EU AI Office directly. The regulator logs in, selects a model and date range, and gets a cryptographically verified evidence package — no bank staff involved, no PDF export, no email.
Why this is exit-critical: it converts RiskAttest from a tool banks buy to a tool banks cannot remove. Once a regulator has accessed the portal, the bank cannot migrate to a different vendor without re-doing every audit trail. The switching cost becomes existential. This is the feature that turns ARR into retention, and retention is what acquirers pay multiples on.
Build complexity: medium. It's a scoped read-only view of the existing evidence vault with time-bounded JWT tokens and an audit log of regulator access.
Assume default Bank-level IT & Secrity environment. Deployment or Pilot for evey feature has to be smooth. Market-research on this first.

- Feature 2: Continuous model monitoring with drift-triggered re-validation
Current state: RiskAttest validates models at a point in time. What regulators are moving toward (SR 26-2 explicitly, CPS 230 implicitly) is continuous validation — the model must be monitored in production and re-validated automatically when it drifts beyond defined thresholds.
According to a 2024 global survey, 40% of technology executives believed their AI governance program was insufficient, and by 2026, Gartner projects that AI models from organizations that operationalize AI transparency, trust, and security will achieve a 50% increase in adoption and business goal achievement.
The gap: Databricks acquired TruEra specifically for AI model monitoring in May 2024, but TruEra has no regulatory compliance angle. If RiskAttest adds drift-triggered re-validation that automatically generates a new EvidencePacket and notifies the GRC platform, it becomes the only tool that closes the loop between production monitoring and regulatory evidence — something neither Databricks nor ValidMind currently does. KPMG
Build complexity: medium-high. Requires a polling loop against MLflow/Unity Catalog metrics, threshold configuration per model, and an automated re-validation pipeline.
Assume default Bank-level IT & Secrity environment. Deployment or Pilot for evey feature has to be smooth. Market-research on this first. Integration option to existing cloud-providers (AWS/Azure/DataBricks [mrm already stores its model to DataBricks]) should strongly be considered.

- Feature 3: AI model inventory discovery — "shadow model" detection
Most banks don't know how many models they're running. The average bank uses 175 different quantitative models, but the number actually deployed in production and generating decisions is typically higher than the number in the model inventory — because engineers deploy models that never get registered. deloitte
A scanner that connects to Databricks, SageMaker, Azure ML, and S3 and surfaces models that are running but absent from the mrm_project.yml is a category-defining feature. It turns RiskAttest from a compliance tool into the system of record for the model inventory itself — which is exactly the position Moody's Analytics and IBM OpenPages are trying to own from the GRC side, but cannot reach from there because they don't have MLOps connectivity.
This is the feature most likely to trigger an acqui-hire conversation with Moody's specifically. Moody's Model Risk and Governance Solutions are explicitly designed to assist companies with their model lifecycle management needs — but they're doing it from the top down (framework + policy), not bottom-up (actual running models). RiskAttest can do it bottom-up. bankofengland
Build complexity: medium. Requires read-only API connectors to the major MLOps platforms and a diff engine against the declared model inventory.

- Feature 4: Adversarial test pack — 50+ templates for APRA/Fed exam prep
Current: 14 GenAI tests, CCR, tabular, credit risk packs. What's missing: a curated library of adversarial scenarios specifically designed around what APRA examiners and Fed horizontal reviewers actually ask for in MRM exams. These are not generic tests — they're exam-specific scenarios that a bank's model risk team can run six weeks before an examination to identify gaps.
It is no longer enough to tick compliance boxes; institutions must embed efficiency, transparency, and resilience into governance by streamlining lifecycles, automating validation, and ensuring continuous monitoring. Moody's
This feature is low-build but high-commercial value because it requires domain expertise (Gaurav's network) rather than engineering. It's the feature that justifies a premium tier and a services attach — "run our exam prep suite, get a readiness report, present it to your board."
Build complexity: low-medium. Mostly YAML test definitions + documentation. The infrastructure is already there.

- Feature 5: SOC 2 Type II + ISO 42001 certification
Not a product feature, but treated as one by enterprise buyers. TruEra achieved SOC 2 Type II certification in October 2023 and that was cited as a key credential in their Snowflake acquisition. For a tool that stores cryptographic evidence of bank model decisions, the absence of SOC 2 Type II is a procurement blocker at any institution above community bank size. Solytics-partners
ISO/IEC 42001 (AI management systems) is the emerging standard for AI governance and will be required by the EU AI Act implementation. AI governance platforms that support ISO/IEC 42001 are increasingly differentiated in enterprise procurement. RiskAttest being the first MRM-specific tool certified against ISO 42001 is a meaningful wedge — none of the incumbents have it yet. PwC
Build complexity: zero (it's a process + audit engagement, not code). Timeline 6–9 months from seed close.

## Planned

- GRC platform connectors (OpenPages, ServiceNow IRM, Workiva)
- Quant worked examples: XVA via ORE, IRB credit risk (PD/LGD/EAD)
- Crosswalk auto-update from authoritative regulator sources

## Contributing

PRs welcome — see [CONTRIBUTING.md](mrm-core/CONTRIBUTING.md) and
[GOVERNANCE.md](mrm-core/GOVERNANCE.md). Architecture-significant
changes need an ADR under
[docs/adr/](mrm-core/docs/adr/).
