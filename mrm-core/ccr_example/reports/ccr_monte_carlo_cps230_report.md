# CPS 230 Model Validation Report

| Field | Value |
|-------|-------|
| **Model** | ccr_monte_carlo |
| **Version** | 1.0.0 |
| **Report Date** | 2026-06-18 08:43 UTC |
| **Regulatory Framework** | APRA CPS 230 -- Operational Risk Management |
| **Risk Tier** | tier_1 |
| **Owner** | market_risk_team |
| **Validation Frequency** | quarterly |

---

## 1. Executive Summary

This report presents the independent validation results for the
**ccr_monte_carlo** model (v1.0.0)
conducted on 2026-06-18.

The validation was performed in accordance with **APRA CPS 230 -- Operational Risk Management**
requirements.  The model is classified as
**Tier 1**
(high materiality,
high complexity), requiring
quarterly validation.

### Overall Result: **FAILED**

| Metric | Value |
|--------|-------|
| Tests Executed | 8 |
| Tests Passed | 5 |
| Tests Failed | 3 |
| Pass Rate | 62.5% |
| Validation Status | **FAIL** |

**Model Purpose:** Vanilla Monte Carlo simulation engine for Counterparty Credit Risk. Simulates interest rate swap mark-to-market paths under Vasicek rate dynamics to compute EPE, PFE, CVA, and EAD for OTC derivative portfolios.


**Methodology:** monte_carlo_simulation

## 2. Model Inventory Card

### 2.1 Identification

| Field | Value |
|-------|-------|
| Model Name | ccr_monte_carlo |
| Version | 1.0.0 |
| Owner | market_risk_team |
| Use Case | counterparty_credit_risk |
| Methodology | monte_carlo_simulation |
| Risk Tier | tier_1 |
| Materiality | high |
| Complexity | high |
| Validation Frequency | quarterly |

### 2.2 Model Parameters

| Parameter | Value |
|-----------|-------|
| n_simulations | 5000 |
| n_time_steps | 60 |
| dt | 0.0833 |
| confidence_level | 0.95 |
| rate_model | vasicek |
| kappa | 0.15 |
| theta | 0.03 |
| sigma | 0.01 |
| r0 | 0.025 |

### 2.3 CPS 230 Classification Rationale

Per **CPS 230 Para 8-10**, models must be classified by materiality and
complexity.  This model is classified as Tier 1 because:

- It computes regulatory capital metrics (EAD, CVA) used in prudential returns
- It uses Monte Carlo simulation requiring careful convergence control
- Errors in exposure estimates directly impact capital adequacy ratios
- The model covers OTC derivative portfolios with material notional exposure

## 3. CPS 230 Compliance Matrix

The following matrix maps each CPS 230 requirement to the validation
evidence demonstrating compliance.

| CPS 230 Ref | Requirement | Status | Evidence |
|-------------|-------------|--------|----------|
| Para 8-10 | Risk Identification and Classification | SATISFIED | compliance.GovernanceCheck: PASS -- {'risk_tier_assigned': 'CPS 230 Para 8-10', 'owner_designated': 'CPS 230 Para 11', 'validation_frequency_set': 'CPS 230 Para 12-14', 'use_case_documented': 'CPS 230 Para 15', 'methodology_documented': 'CPS 230 Para 16', 'version_controlled': 'CPS 230 Para 28'}; Config: Model classified as Tier 1 (high materiality, high complexity) |
| Para 11 | Accountability and Ownership | DOCUMENTED | Config: Owner: market_risk_team; escalation path defined |
| Para 12-14 | Validation Frequency and Scope | DOCUMENTED | Config: Quarterly validation with full test suite |
| Para 15-18 | Risk Assessment Methodology | NOT SATISFIED | ccr.EPEReasonableness: FAIL -- CPS 230 Para 15-18: Risk identification and assessment; Config: EPE reasonableness test validates exposure bounds |
| Para 19-23 | Concentration and Interconnection Risk | SATISFIED | ccr.WrongWayRisk: PASS -- CPS 230 Para 19-23: Concentration and interconnection risk; Config: Wrong-way risk test detects PD-exposure correlation |
| Para 24-27 | Scenario Analysis and Stress Testing | SATISFIED | ccr.CVASensitivity: PASS -- CPS 230 Para 24-27: Scenario analysis and stress testing; Config: CVA sensitivity test with PD shocks |
| Para 28-29 | Model Adequacy and Fitness-for-Purpose | NOT SATISFIED | ccr.ExposureProfileShape: FAIL -- CPS 230 Para 28-29: Model adequacy and fitness-for-purpose; Config: Exposure profile shape validation |
| Para 30-33 | Operational Risk Controls | SATISFIED | ccr.MCConvergence: PASS -- CPS 230 Para 30-33: Operational risk controls for model computation; Config: MC convergence test ensures computational reliability |
| Para 34-37 | Ongoing Monitoring and Reporting | NOT SATISFIED | ccr.PFEBacktest: FAIL -- CPS 230 Para 34-37: Ongoing monitoring and reporting; Config: Automated trigger system: scheduled, breach, drift, materiality |
| Para 38-42 | Risk Mitigation and Controls | SATISFIED | ccr.CollateralEffectiveness: PASS -- CPS 230 Para 38-42: Risk mitigation and controls; Config: Collateral effectiveness test validates CSA modelling |

### 3.1 Compliance Summary

Each row above corresponds to a specific paragraph of APRA CPS 230.
Tests are designed to provide quantitative evidence that the model
satisfies the operational risk management requirements of the standard.
Where a requirement is marked "SATISFIED", the corresponding validation
test has passed with results within acceptable thresholds.

## 4. Detailed Test Results

### 4.1 ccr.MCConvergence

| Field | Value |
|-------|-------|
| Status | **PASS** |
| Score | 0.9720 |
| CPS 230 Reference | Para 30-33: Operational Risk Controls |

**Evidence Details:**

```json
{
  "epe_sample_a": 258694.90280232587,
  "epe_sample_b": 266051.82981546986,
  "relative_difference": 0.02804,
  "threshold": 0.05,
  "n_simulations": 5000
}
```

**Regulatory Mapping:** CPS 230 Para 30-33: Operational risk controls for model computation

### 4.2 ccr.EPEReasonableness

| Field | Value |
|-------|-------|
| Status | **FAIL** |
| Score | 0.7800 |
| CPS 230 Reference | Para 15-18: Risk Assessment Methodology |
| Failure Reason | 22% of EPE/notional ratios outside [0.001, 0.1] |

**Evidence Details:**

```json
{
  "mean_epe_notional_ratio": 0.017477,
  "min_ratio_observed": 0.0,
  "max_ratio_observed": 0.077223,
  "outlier_count": 11,
  "outlier_pct": 0.22,
  "bounds": [
    0.001,
    0.1
  ],
  "n_counterparties": 50
}
```

**Regulatory Mapping:** CPS 230 Para 15-18: Risk identification and assessment

### 4.3 ccr.PFEBacktest

| Field | Value |
|-------|-------|
| Status | **FAIL** |
| Score | 0.8600 |
| CPS 230 Reference | Para 34-37: Ongoing Monitoring and Reporting |
| Failure Reason | PFE breach rate 14.00% exceeds 10% threshold |

**Evidence Details:**

```json
{
  "breach_rate": 0.14,
  "n_breaches": 7,
  "n_observations": 50,
  "max_breach_rate": 0.1,
  "mean_pfe": 599091.71,
  "mean_realised": 72349.92,
  "confidence_level": 0.95
}
```

**Regulatory Mapping:** CPS 230 Para 34-37: Ongoing monitoring and reporting

### 4.4 ccr.CVASensitivity

| Field | Value |
|-------|-------|
| Status | **PASS** |
| Score | 0.8926 |
| CPS 230 Reference | Para 24-27: Scenario Analysis and Stress Testing |

**Evidence Details:**

```json
{
  "pd_bump_pct": 50.0,
  "mean_sensitivity": 0.8926,
  "sensitivities": [
    1.0091,
    0.7494,
    0.9193
  ],
  "base_cvas": [
    5775.66,
    11350.68,
    469.41
  ],
  "shocked_cvas": [
    8689.82,
    15603.56,
    685.18
  ],
  "bounds": [
    0.1,
    3.0
  ]
}
```

**Regulatory Mapping:** CPS 230 Para 24-27: Scenario analysis and stress testing

### 4.5 ccr.WrongWayRisk

| Field | Value |
|-------|-------|
| Status | **PASS** |
| Score | 0.7752 |
| CPS 230 Reference | Para 19-23: Concentration and Interconnection Risk |

**Evidence Details:**

```json
{
  "pd_exposure_correlation": -0.2248,
  "max_correlation": 0.6,
  "risk_level": "LOW",
  "n_counterparties": 50
}
```

**Regulatory Mapping:** CPS 230 Para 19-23: Concentration and interconnection risk

### 4.6 ccr.ExposureProfileShape

| Field | Value |
|-------|-------|
| Status | **FAIL** |
| Score | 0.6000 |
| CPS 230 Reference | Para 28-29: Model Adequacy and Fitness-for-Purpose |
| Failure Reason | Exposure profile shape anomaly detected |

**Evidence Details:**

```json
{
  "has_peak": false,
  "peak_time_step": 0,
  "coefficient_of_variation": 0.3074,
  "not_flat": true,
  "no_negatives": true,
  "profile_length": 60,
  "peak_ee": 449583.33,
  "terminal_ee": 127748.66
}
```

**Regulatory Mapping:** CPS 230 Para 28-29: Model adequacy and fitness-for-purpose

### 4.7 ccr.CollateralEffectiveness

| Field | Value |
|-------|-------|
| Status | **PASS** |
| Score | 0.2836 |
| CPS 230 Reference | Para 38-42: Risk Mitigation and Controls |

**Evidence Details:**

```json
{
  "mean_epe_collateralised": 70849.09,
  "mean_epe_uncollateralised": 209868.45,
  "epe_notional_ratio_coll": 0.014945,
  "epe_notional_ratio_uncoll": 0.020862,
  "effective_reduction_pct": 28.36,
  "n_collateralised": 29,
  "n_uncollateralised": 21
}
```

**Regulatory Mapping:** CPS 230 Para 38-42: Risk mitigation and controls

### 4.8 compliance.GovernanceCheck

| Field | Value |
|-------|-------|
| Status | **PASS** |
| Score | 1.0000 |
| CPS 230 Reference | Para 8-10: Risk Identification and Classification |

**Evidence Details:**

```json
{
  "checks": {
    "risk_tier_assigned": true,
    "owner_designated": true,
    "validation_frequency_set": true,
    "use_case_documented": true,
    "methodology_documented": true,
    "version_controlled": true
  },
  "checks_passed": 6,
  "checks_total": 6,
  "standard": "cps230"
}
```

**Regulatory Mapping:** {'risk_tier_assigned': 'CPS 230 Para 8-10', 'owner_designated': 'CPS 230 Para 11', 'validation_frequency_set': 'CPS 230 Para 12-14', 'use_case_documented': 'CPS 230 Para 15', 'methodology_documented': 'CPS 230 Para 16', 'version_controlled': 'CPS 230 Para 28'}


## 5. Validation Triggers (CPS 230 Para 34-37)

Re-validation is triggered automatically when any of the following
conditions are met.  This implements the CPS 230 requirement for
ongoing monitoring and timely response to material changes.

### 5.1 Configured Triggers

| Type | Description | Threshold | Compliance Ref |
|------|-------------|-----------|----------------|
| scheduled | Quarterly scheduled re-validation | 90 | CPS 230 Para 34: Periodic review frequency |
| breach | PFE back-test breach rate exceeds 10% | 0.1 | CPS 230 Para 36: Breach-driven re-validation |
| drift | Monte Carlo output drift exceeds 15% | 0.15 | CPS 230 Para 35: Material change detection |
| materiality | Portfolio notional or counterparty count changes > 20% | 0.2 | CPS 230 Para 37: Materiality-driven review |
| regulatory | APRA CPS 230 amendment or prudential guidance update | N/A | CPS 230 Para 42: Regulatory change response |

### 5.2 Active Trigger Events

| Trigger ID | Type | Fired At | Reason | Status |
|------------|------|----------|--------|--------|
| SCHED-ccr_monte_carlo-20260618 | scheduled | 2026-06-18T08:43:23 | Scheduled re-validation: 90 days since last run | fired |
| BREACH-ccr_monte_carlo-202606180843 | breach | 2026-06-18T08:43:23 | PFE breach rate 14.00% exceeds 10% | fired |

### 5.3 Re-validation Schedule

Per CPS 230 Para 12-14, Tier 1 models require quarterly validation.
The trigger system supplements scheduled validation with event-driven
re-validation when:

- Back-test breaches exceed the defined threshold
- Model output drift is detected beyond tolerance
- Portfolio composition changes materially
- Regulatory amendments require model review

## 6. Findings, Limitations, and Recommendations

### 6.1 Findings

- **ccr.EPEReasonableness**: 22% of EPE/notional ratios outside [0.001, 0.1]
- **ccr.PFEBacktest**: PFE breach rate 14.00% exceeds 10% threshold
- **ccr.ExposureProfileShape**: Exposure profile shape anomaly detected

### 6.2 Model Limitations

- The model uses a simplified Vasicek rate process; more complex rate
  dynamics (e.g., Hull-White, LMM) may be warranted for exotic products
- Collateral modelling assumes instantaneous margin calls; margin period
  of risk is approximated
- Wrong-way risk detection is based on portfolio-level correlation;
  name-specific wrong-way risk requires additional analysis
- The model does not currently support multi-currency netting sets

### 6.3 Recommendations

- Remediate ccr.EPEReasonableness failure ((Para 15-18))
- Remediate ccr.PFEBacktest failure ((Para 34-37))
- Remediate ccr.ExposureProfileShape failure ((Para 28-29))
- Escalate findings to model owner and risk committee per CPS 230 Para 11

## 7. Approval and Sign-off

### CPS 230 Para 11: Accountability

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Model Owner | market_risk_team | ___/___/______ | _______________ |
| Independent Validator | _______________ | ___/___/______ | _______________ |
| Chief Risk Officer | _______________ | ___/___/______ | _______________ |
| Head of Model Risk | _______________ | ___/___/______ | _______________ |

### Attestation

I confirm that this validation has been conducted in accordance with
APRA CPS 230 requirements and the institution's Model Risk Management
Policy.  The findings and recommendations above are a true and accurate
representation of the validation outcomes.

---

*Report generated: 2026-06-18 08:43 UTC*
*MRM Framework Version: 0.1.0*
*Regulatory Framework: APRA CPS 230 -- Operational Risk Management*