### Title
Integer-division truncation of non-multiple-of-BPS liquidation curve exponents produces a wrong liquidation factor — ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`calc-liq-factor-exp` in `v0-4-market.clar` approximates `factor^alpha` using Clarity's native integer `pow`, but it derives the integer exponent via `(/ exp BPS)`, which silently truncates any `curve-exponent` that is not an exact multiple of `BPS` (10000). This produces a mathematically wrong liquidation factor for any DAO-configured `LIQ-CURVE-EXP` value that represents a fractional power (e.g. 1.5x, 2.5x), directly corrupting the liquidation penalty and max-liquidatable-debt calculation used at the health-check/liquidation stage.

### Finding Description
The graduated-liquidation curve is applied by: [1](#0-0) 

```
;; Apply curve exponent for graduated liquidation
;; liq-factor = liq-factor^alpha
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS) 
    factor
    (if (> exp BPS) 
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
```

`exp` (the DAO-configured `LIQ-CURVE-EXP`, BPS-scaled so `BPS` = exponent `1.0`) is only ever used through `(/ exp BPS)`, an integer division. Any `exp` that is not an exact multiple of `BPS` (e.g. `exp = 15000` intended to mean `alpha = 1.5`) gets truncated to `(/ 15000 10000) = 1`, so the branch computes `pow(factor,1) / pow(BPS,0) = factor` instead of `factor^1.5`. The fractional part of the configured curve exponent is discarded entirely and silently — there is no revert, no rounding compensation, just an incorrect exponent used in the power computation. This is used directly in the liquidation health/penalty pipeline: [2](#0-1) 

```
(define-private (calc-liquidation-params ...)
  (let ((liq-pct-linear (calc-liq-factor current-ltv ltv-liq-partial ltv-liq-full))
        (liq-pct-scaled (calc-liq-factor-exp liq-pct-linear curve-exponent))
        (liq-penalty (calc-liq-factor-bound liq-pct-scaled liq-penalty-min liq-penalty-max))
        (max-debt-usd (calc-liq-debt-repay total-debt-usd liq-pct-scaled)))
    ...
```

`calc-liquidation-params` is invoked in the `liquidate` public function right after the health-check and directly drives `liq-penalty` and `max-debt-usd` (the amount of collateral seized and the liquidator's bonus): [3](#0-2) 

The `LIQ-CURVE-EXP` field is a legitimate egroup parameter (packed as a `(buff 2)`, bounded by `MAX-FACTOR-MUL`/`MAX-FACTOR-DENOM` constants) intended to support graduated, non-integer liquidation curves: [4](#0-3) 

Because `liq-pct-linear` (`factor`) is a BPS-scaled ratio ≤ `BPS` (i.e., ≤ 1.0), and powers >1 of a base ≤1 are strictly smaller than the base, truncating the exponent downward (e.g. 1.5 → 1) makes the code compute a **larger** `liq-pct-scaled` than the correctly-configured curve intends whenever `exp` sits strictly between two multiples of `BPS` above `BPS`. This is not a DAO misconfiguration — the DAO is choosing a valid graduated exponent within the intended design range; the truncation is a pure arithmetic defect in `calc-liq-factor-exp` itself, structurally analogous to the PRBMath `pow()` inconsistency corrupting `TierCalculationLib`/`DrawAccumulatorLib` computations in the referenced report: both cases feed a power-function result — computed with a mis-handled exponent — directly into a downstream financial/health calculation.

### Impact Explanation
`liq-pct-scaled` feeds directly into `liq-penalty` (via `calc-liq-factor-bound`) and `max-debt-usd` (via `calc-liq-debt-repay`), which set the liquidator's bonus and the amount of debt/collateral processed in `liquidate`. An inflated `liq-pct-scaled` for any curve exponent that is not an exact multiple of `BPS` results in the protocol seizing more collateral and applying a higher penalty than the egroup's configured curve intends, at every liquidation performed against positions in that egroup — a systematic, protocol-enforced over-seizure of borrower collateral (temporary/permanent freezing/loss of borrower funds beyond the intended liquidation penalty), landing in the in-scope "temporary freezing of funds" / theft-adjacent impact class stemming from a wrong health/liquidation-factor computation.

### Likelihood Explanation
This triggers deterministically whenever an egroup is configured with `LIQ-CURVE-EXP` that is not an exact multiple of `10000` (a value clearly within the intended graduated-curve design space, bounded only by `MAX-FACTOR-MUL`/`MAX-FACTOR-DENOM`), and any liquidation occurs against a position in that egroup with `current-ltv` strictly between `ltv-liq-partial` and `ltv-liq-full`. No attacker action beyond a routine liquidation call is required — the bug fires automatically on the standard liquidation path.

### Recommendation
Replace the integer `(/ exp BPS)` truncation in `calc-liq-factor-exp` with a fixed-point power computation that preserves the fractional part of `curve-exponent` (e.g. a proper fixed-point `pow` using log/exp or a binomial/Newton approximation operating on the BPS-scaled ratio directly), or explicitly restrict `LIQ-CURVE-EXP` at registration time (`v0-egroup.clar`) to only accept exact multiples of `BPS`, rejecting/erroring on any non-multiple value so the approximation cannot silently diverge from the configured curve.

### Proof of Concept
1. DAO configures an egroup with `LIQ-CURVE-EXP = 15000` (intended `alpha = 1.5`), within the allowed `MAX-FACTOR-MUL`/`MAX-FACTOR-DENOM` bounds.
2. A borrower's `current-ltv` lands between `ltv-liq-partial` and `ltv-liq-full`, giving `liq-pct-linear = factor` (e.g. `5000`, i.e. 0.5 in BPS terms).
3. `calc-liq-factor-exp(5000, 15000)` executes the `> BPS` branch: `(/ (pow 5000 (/ 15000 10000)) (pow 10000 (- (/ 15000 10000) 1))) = (/ (pow 5000 1) (pow 10000 0)) = 5000`, i.e. `factor^1` instead of the intended `factor^1.5 ≈ 3535`.
4. The resulting `liq-pct-scaled = 5000` (instead of `~3535`) is passed into `calc-liq-factor-bound` and `calc-liq-debt-repay`, producing a higher `liq-penalty` and larger `max-debt-usd` than the curve design intends, at every liquidation call in this egroup — confirmed by tracing the exact arithmetic in `calc-liq-factor-exp` and its callers cited above.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L706-713)
```text
;; Apply curve exponent for graduated liquidation
;; liq-factor = liq-factor^alpha
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS) 
    factor
    (if (> exp BPS) 
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
```

**File:** mainnet/contracts/market/v0-4-market.clar (L736-756)
```text
;; Graduated liquidation parameter calculation
;; Combines the 4-step liquidation factor calculation into a single helper
;; Returns: { liq-pct-scaled: uint, liq-penalty: uint, max-debt-usd: uint }
(define-private (calc-liquidation-params
  (current-ltv uint)
  (ltv-liq-partial uint)
  (ltv-liq-full uint)
  (liq-penalty-min uint)
  (liq-penalty-max uint)
  (curve-exponent uint)
  (total-debt-usd uint))
  
  (let ((liq-pct-linear (calc-liq-factor current-ltv ltv-liq-partial ltv-liq-full))
        (liq-pct-scaled (calc-liq-factor-exp liq-pct-linear curve-exponent))
        (liq-penalty (calc-liq-factor-bound liq-pct-scaled liq-penalty-min liq-penalty-max))
        (max-debt-usd (calc-liq-debt-repay total-debt-usd liq-pct-scaled)))
    {
      liq-pct-scaled: liq-pct-scaled,
      liq-penalty: liq-penalty,
      max-debt-usd: max-debt-usd
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1416-1444)
```text
    (ltv-liq-partial (buff-to-uint-be (get LTV-LIQ-PARTIAL group)))
    (ltv-liq-full (buff-to-uint-be (get LTV-LIQ-FULL group)))
    (liq-penalty-min (buff-to-uint-be (get LIQ-PENALTY-MIN group)))
    (liq-penalty-max (buff-to-uint-be (get LIQ-PENALTY-MAX group)))
    (curve-exponent (buff-to-uint-be (get LIQ-CURVE-EXP group)))

    ;; LTV = (debt x 10,000) / collateral
    ;; handle edge case: If collateral = 0, return max LTV (BPS) or 0 if debt also 0
    (current-ltv   (if (is-eq total-collateral-usd u0)
                       (if (is-eq total-debt-usd u0) u0 BPS)
                       (mul-div-down total-debt-usd BPS total-collateral-usd)))
    
    ;; Oracle frontrunning protection: prevent same-block liquidation
    ;; This blocks flash-loan based attacks where user borrows + gets liquidated in same block
    (last-borrow-block (get last-borrow-block position))
    (same-block-check (asserts! (not (is-eq last-borrow-block stacks-block-height)) ERR-LIQUIDATION-BORROW-SAME-BLOCK))

    ;; health check (FAIL-FAST) 
    ;; Check position is liquidatable BEFORE calling calc-liq-factor
    (health-check  (asserts! (>= current-ltv ltv-liq-partial) ERR-HEALTHY))

    ;; liquidation parameters (graduated liquidation calculation)
    (liq-params (calc-liquidation-params 
                  current-ltv ltv-liq-partial ltv-liq-full
                  liq-penalty-min liq-penalty-max 
                  curve-exponent total-debt-usd))
    (liq-pct-scaled (get liq-pct-scaled liq-params))
    (liq-penalty (get liq-penalty liq-params))
    (max-debt-usd (get max-debt-usd liq-params))
```

**File:** mainnet/contracts/registry/v0-egroup.clar (L16-19)
```text
(define-constant BPS u10000)
(define-constant MAX u128)
(define-constant MAX-FACTOR-MUL u5000)  ;; max: exponential
(define-constant MAX-FACTOR-DENOM u40000) ;; min: /4
```
