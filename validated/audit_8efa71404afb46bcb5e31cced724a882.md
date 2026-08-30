### Title
Integer truncation of the liquidation curve exponent in `calc-liq-factor-exp` silently discards the DAO-configured curve shape, causing over-liquidation of borrowers - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`calc-liq-factor-exp` is meant to raise the linear liquidation factor to a configurable power `alpha` (curve exponent), so the graduated liquidation ramp between `ltv-liq-partial` and `ltv-liq-full` follows the shape the DAO configured via `LIQ-CURVE-EXP`. Instead of preserving the fractional part of `alpha`, the function performs an **integer division of the scaled exponent before using it in the exponentiation math**, silently rounding `alpha` down to the nearest supported integer (or collapsing to a fixed `0.5` for any sub-1.0 value). Because the liquidation percentage base `factor` is always `<= BPS` (i.e. `<1.0` in real terms until the position is fully at `ltv-liq-full`), using a smaller-than-configured exponent always *increases* the computed liquidation percentage, penalty, and max debt repayable — systematically over-liquidating borrowers relative to the DAO's intended curve.

### Finding Description
```clarity
;; mainnet/contracts/market/v0-4-market.clar
;; Apply curve exponent for graduated liquidation
;; liq-factor = liq-factor^alpha
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS) 
    factor
    (if (> exp BPS) 
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
``` [1](#0-0) 

`exp` is `LIQ-CURVE-EXP`, a BPS-scaled representation of the intended exponent `alpha` (`BPS` = `10000` meaning `alpha = 1.0`). When `exp > BPS` (i.e. `alpha > 1.0`, e.g. `alpha = 1.5` → `exp = 15000`), the code computes `(/ exp BPS)`, which **truncates the fractional part of alpha before it is used as the actual integer power in `pow`**. For `exp = 15000`, `(/ exp BPS)` evaluates to `u1`, so the whole branch collapses to `(/ (pow factor 1) (pow BPS 0))`, i.e. simply `factor` — identical to the pure-linear case (`alpha = 1.0`) even though the DAO configured `alpha = 1.5`. The same truncation happens for any `exp` that is not an exact multiple of `BPS` (e.g. `exp = 25000` intended as `alpha = 2.5` truncates to `alpha = 2.0`), and for `exp < BPS` the function always applies a fixed square-root (`alpha = 0.5`) regardless of the actual configured sub-`BPS` value.

This function is called from `calc-liquidation-params`, which feeds directly into the on-chain `liquidate` public function: [2](#0-1) [3](#0-2) 

`liq-pct-scaled` (the truncated-exponent value) drives both the liquidation penalty (`calc-liq-factor-bound`) and the maximum liquidatable debt (`calc-liq-debt-repay`), both of which determine the actual token amounts transferred during liquidation: [4](#0-3) 

Since `factor` (the linear liquidation ratio) is bounded in `[0, BPS]` and represents a value `<1.0` for any position that has not yet reached `ltv-liq-full`, using a smaller-than-configured `alpha` (via truncation) always produces `factor^(truncated alpha) >= factor^(configured alpha)`. This means `liq-pct-scaled`, `liq-penalty`, and `max-debt-usd` are all inflated relative to the DAO's intended curve for every `alpha` value that is not an exact multiple of `BPS` (or, for sub-1.0 alphas, not exactly `5000`).

### Impact Explanation
The direction of the error consistently benefits the **liquidator** at the expense of the **borrower**: the liquidator is permitted to repay more debt and seize more collateral (with the associated bonus penalty) than the DAO's configured graduated curve intended, for any partially-liquidatable position. This results in a direct, larger-than-intended transfer of the borrower's collateral to the liquidator on every liquidation where the configured curve exponent is fractional — a theft of user collateral in motion during liquidation. This lands on the Critical impact class ("direct theft of user funds ... in motion").

### Likelihood Explanation
The bug triggers on essentially every liquidation event once the DAO configures a non-integer-multiple curve exponent (which the code comments and the dedicated `sqrti`/"assume factor^0.5" branch show is an explicitly supported, intended feature, not a misconfiguration). No special conditions, front-running, or oracle manipulation are required — any ordinary partial liquidation on an egroup with `LIQ-CURVE-EXP != BPS` and not equal to an exact integer multiple of `BPS` (or exactly `5000` for the sub-linear case) is affected. This makes the issue continuously and reliably exploitable/observable rather than a rare edge case.

### Recommendation
Preserve the fractional precision of the curve exponent instead of truncating it via integer division before exponentiation. Either restrict `LIQ-CURVE-EXP` to only exact integer multiples of `BPS` (documented and enforced at config-set time) so the current integer `pow` logic is always exact, or replace the exponentiation with a fixed-point power function (e.g. `exp(alpha * ln(factor))` in fixed-point, or a binomial/Taylor approximation) that correctly consumes the full BPS-scaled `alpha` without discarding its fractional component.

### Proof of Concept
1. DAO configures an egroup with `LIQ-CURVE-EXP = 15000` (intended `alpha = 1.5`, a legitimate graduated curve config per the code's own design intent).
2. A borrower's position crosses into partial liquidation range with `liq-pct-linear (factor) = 5000` (50% of the way from `ltv-liq-partial` to `ltv-liq-full`).
3. Intended: `liq_pct_scaled = 0.5^1.5 * BPS ≈ 3536`.
4. Actual (per `calc-liq-factor-exp`): `(/ 15000 10000) = u1` → `(pow factor 1) / (pow BPS 0) = factor = 5000`.
5. The liquidator receives a `liq-pct-scaled` of `5000` instead of the intended `~3536`, inflating `max-debt-usd` (via `calc-liq-debt-repay`) and `liq-penalty` (via `calc-liq-factor-bound`) proportionally, letting the liquidator repay/seize more debt/collateral than the DAO's configured curve intended — profiting the liquidator and disadvantaging the borrower on every such liquidation.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L715-724)
```text
;; Scale penalty between min and max using liquidation factor
;; liq-penalty = liq-penalty-min + (liq-factor * (liq-penalty-max - liq-penalty-min) / BPS)
;; Capped at bound-max to handle cases where liq-factor > BPS
(define-private (calc-liq-factor-bound (liq-factor uint) (bound-min uint) (bound-max uint))
  (min bound-max (+ bound-min (mul-bps-down liq-factor (- bound-max bound-min)))))

;; Calculate debt to repay based on liquidation factor
;; debt-repay = liq-factor * debt / BPS
(define-private (calc-liq-debt-repay (debt uint) (liq-factor uint)) 
  (mul-bps-down liq-factor debt))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L739-756)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1437-1444)
```text
    ;; liquidation parameters (graduated liquidation calculation)
    (liq-params (calc-liquidation-params 
                  current-ltv ltv-liq-partial ltv-liq-full
                  liq-penalty-min liq-penalty-max 
                  curve-exponent total-debt-usd))
    (liq-pct-scaled (get liq-pct-scaled liq-params))
    (liq-penalty (get liq-penalty liq-params))
    (max-debt-usd (get max-debt-usd liq-params))
```
