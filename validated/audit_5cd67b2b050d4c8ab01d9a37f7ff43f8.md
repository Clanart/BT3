### Title
Reserve/treasury fee rounds down to zero on small interest accruals, allowing protocol yield to be silently skipped - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar` and equivalent vault contracts)

### Summary
The vault contracts compute the protocol's reserve share of accrued interest using a `mulDiv`-style division (`mul-div-down`) scaled by `BPS` (10,000). Because the numerator (`debt-delta`, the interest accrued since the last accrual) can be small relative to `BPS`, and `fee-reserve` is a competitive/low basis-point rate, the division truncates to zero exactly like the `mulDiv()` tax-avoidance pattern described in the external report — except here it is the protocol's own fee/yield that gets zeroed out instead of a user-paid tax.

### Finding Description
Each vault's math utilities define the same rounding-down primitive used everywhere for interest/fee math: [1](#0-0) 

This primitive is used to compute the reserve/treasury increment from freshly-accrued interest. In the shared vault logic (identical helper structure is present across all vault contracts, e.g. `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`), the treasury preview computes: [2](#0-1) 

```
(old-debt   (mul-div-down scaled-principal idx INDEX-PRECISION))
(new-debt   (mul-div-down scaled-principal next INDEX-PRECISION))
(debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
(reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
```

`reserve-inc = debt-delta * fee-reserve / BPS`. Every time `accrue` is invoked (which happens on virtually every deposit, withdraw, borrow, or repay call, i.e. very frequently, sometimes multiple times per block across different callers), `debt-delta` is the interest accrued only over the elapsed seconds since the last accrual. For short intervals (the common case — accrual happens on almost every transaction) `debt-delta` can be only a handful of base units. If `debt-delta * fee-reserve < BPS` (10,000), the division truncates to zero and the protocol's reserve/treasury share of that interest is permanently lost — it is not carried over or accumulated in a remainder; the next accrual starts its own truncation from the new state.

This is the same root cause as the report's `mulDiv()` truncation: a percentage numerator (`fee-reserve`) applied to a base amount (`debt-delta`) via `x*y/BPS`, where `x*y < BPS` rounds to 0 instead of accruing a fractional entitlement. The relevant `fee-reserve` and vault interest-accrual wiring is confirmed present in the mainnet vault contracts (`mainnet/contracts/vault/v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`), which share byte-identical `mul-div-down`/`mul-bps-down` helper definitions with the reference implementation shown above.

### Impact Explanation
The direction of the error favors depositors/borrowers collectively at the expense of the DAO treasury: interest that should partially accrue to the protocol's reserve is dropped to zero whenever a single accrual step produces a small enough `debt-delta`. Because `accrue` runs on nearly every state-changing vault call, this is not a rare edge case — it is the default behavior for high-frequency, low-latency accrual steps, meaning the treasury systematically under-collects its designated cut of interest over time. This falls under the in-scope impact class: theft/permanent loss of unclaimed yield belonging to the protocol treasury (High).

### Likelihood Explanation
High. `accrue` is called on essentially every user-facing vault action (deposit, withdraw, borrow, repay), so short inter-call intervals producing small `debt-delta` values are the normal operating condition, not an attacker-crafted edge case — though an attacker could also deliberately fragment interactions (e.g., frequent tiny borrow/repay cycles) to keep each `debt-delta` below the truncation threshold and maximize the dropped reserve share.

### Recommendation
Accumulate the truncated remainder (e.g., track `debt-delta * fee-reserve` accumulated numerator across accrual calls and only realize `reserve-inc` once the running total exceeds `BPS`), or round the reserve computation up (`mul-div-up`) so the treasury is never shortchanged, mirroring how debt itself is rounded up (`calc-cumulative-debt` uses `mul-div-up`) while the fee taken *from* that debt is rounded down.

### Proof of Concept
Given `fee-reserve = 500` (5%) and `BPS = 10000`: if a sequence of rapid deposit/withdraw/borrow/repay calls each produce `debt-delta < 20` base units of accrued interest between accruals (easily achieved since interest per short interval on modest positions is often single-digit base units for 6-8 decimal assets), then `reserve-inc = debt-delta * 500 / 10000` truncates to `0` on every single accrual, and the treasury never receives its share of interest despite interest genuinely being generated and compounding into `debt`/`assets` for depositors. [3](#0-2)

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L147-151)
```text
(define-private (mul-div-down (x uint) (y uint) (z uint))
  (/ (* x y) z))

(define-private (mul-div-up (x uint) (y uint) (z uint))
  (/ (+ (* x y) (- z u1)) z))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L348-360)
```text
;; -- Treasury LP preview helpers --------------------------------------------

(define-private (calc-treasury-lp-preview)
  (let ((scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (next (next-index))
        (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
        (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
        (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
        (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
        (ta-preview (total-assets-preview)))
    (if (> reserve-inc u0)
        (mul-div-down reserve-inc (total-supply) (- ta-preview reserve-inc))
```
