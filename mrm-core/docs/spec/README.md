# Normative specifications

Versioned, normative specs for the public contracts of `mrm-core`.

These exist so third parties — auditors, downstream tools, regulators,
and acquirers — can implement against `mrm-core` artefacts without
reading the implementation. The shape of the on-the-wire artefact is
the contract; the implementation is one valid realisation.

The directory layout deliberately mirrors the *Public Review Draft*
(PRD) lifecycle used by the [SR-26.2-MRM
specification](https://github.com/mmpworks/SR-26.2-Model-Risk-Management):

- A spec begins as **PRD-1 (Draft)**.
- It moves to **PRD-2 (Public Review)** once at least two implementers
  acknowledge they are building against it.
- It is **Final** only after a public-comment window closes with no
  open gap-class findings.

Each spec carries:

- A **status** line (Draft / Public Review / Final / Superseded)
- A **version** line (`v1`, `v1.1`, `v2`)
- A **changelog** at the bottom
- **Conformance test vectors** under [`test-vectors/`](test-vectors/)

| Spec | Version | Status |
|---|---|---|
| [Decision Record](replay-record-v1.md) | v1 | Public Review (PRD-2) |
| [Evidence Vault Chain](evidence-vault-v1.md) | v1 | Public Review (PRD-2) |
| [Compliance Plugin Contract](compliance-plugin-v1.md) | v1 | Public Review (PRD-2) |
| [Regulator Evidence Export](regulator-portal-v1.md) | v1 | Draft (PRD-1) |
