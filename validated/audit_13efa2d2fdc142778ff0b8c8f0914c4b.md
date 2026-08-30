### Title
`calc-liq-factor-exp` truncates the liquidation curve exponent, understating the fractional part of alpha and skewing the graduated liquidation factor - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The Sherlock report's root cause is an integer division of a continuously-accumulating quantity (`block.timestamp - lastExpansion`) by a fixed unit (`expansionFrequency`), which truncates the fractional remainder and understates how many "units" have actually elapsed. The analogous defect in Zest is `calc-liq-factor-exp`, which computes the graduated-liquidation exponent `alpha = exp / BPS` using plain integer division before raising `factor` to that power, silently truncating any fractional part of the configured curve exponent.

### Finding Description
`calc-liq-factor-exp` is meant to compute `liq-factor^alpha` where `alpha = curve-exponent / BPS` (BPS = 10000, so an exponent of `15000` should mean `alpha = 1.5`): [1](#0-0) 

```
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS)
    factor
    (if (> exp BPS)
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
```

The expression `(/ exp BPS)` is Clarity integer division and truncates toward zero. For any `curve-exponent` that is not an exact multiple of `BPS` (e.g. `15000` → intended `alpha = 1.5`), `(/ exp BPS)` evaluates to `1`, not `1.5`. The function then computes `pow(factor, 1) / pow(BPS, 0) = factor`, i.e. it silently collapses the intended concave curve (`alpha > 1`) down to the linear case (`alpha = 1`), discarding the fractional exponent entirely — exactly the same class of defect as the report: a division meant to preserve a fractional progression is truncated, and the truncated (smaller) exponent is then used as if it were exact.

This value directly feeds the on-chain liquidation-sizing pipeline: [2](#0-1) 

```
(define-private (calc-liquidation-params ...)
  (let ((liq-pct-linear (calc-liq-factor current-ltv ltv-liq-partial ltv-liq-full))
        (liq-pct-scaled (calc-liq-factor-exp liq-pct-linear curve-exponent))
        (liq-penalty (calc-liq-factor-bound liq-pct-scaled liq-penalty-min liq-penalty-max))
        (max-debt-usd (calc-liq-debt-repay total-debt-usd liq-pct-scaled)))
    ...))
```

`liq-pct-scaled` is used both to size `max-debt-usd` (how much debt can be liquidated) and, via `calc-liq-factor-bound`, to set `liq-penalty` (the liquidator's bonus). Because `factor` is always ≤ `BPS` (≤ 1.0 in fractional terms), raising it to a *smaller-than-intended* exponent (1 instead of the intended 1.x) produces a **larger** `liq-pct-scaled` than the curve designer intended for partial-liquidation LTVs between the partial and full thresholds. A larger `liq-pct-scaled` in turn produces a larger `max-debt-usd` and a larger `liq-penalty`, which flows into `calc-liq-collateral-repay`: [3](#0-2) 

so more of the borrower's collateral is authorized to be seized, and a bigger bonus is paid to the liquidator, than the configured graduated curve intends.

### Impact Explanation
This is a rounding-direction defect inside the notional/liquidation-sizing math (analogous to the "rounding direction" and "exponent conversion" bug classes called out as in-scope): any `curve-exponent` configured with a fractional alpha (e.g. `15000`, `12500`, `17500` bps) is silently treated as `alpha = floor(exp/BPS)`, producing a systematically inflated `liq-pct-scaled`/`liq-penalty` for LTVs between the partial and full liquidation thresholds. This causes the protocol to authorize seizing more collateral and pay a larger liquidator bonus than the graduated curve was designed to allow, at the direct expense of the borrower being liquidated — a theft of the borrower's collateral value beyond the protocol's own risk parameters. The liquidator (caller) is the party that profits from the excess seizure/bonus.

### Likelihood Explanation
Likelihood is high whenever the DAO/admin configures a `curve-exponent` that is not an exact multiple of `BPS` (any non-integer alpha, which is the whole point of offering a graduated/concave liquidation curve rather than a flat linear one). Every liquidation executed while `current-ltv` sits strictly between `ltv-liq-partial` and `ltv-liq-full` (the intended graduated region) will use the truncated, wrong exponent — this is not an edge case but the normal operating range of the graduated-liquidation feature.

### Recommendation
Compute the exponentiation using a fixed-point representation of `alpha` (retain the BPS-scaled remainder rather than truncating it away) — e.g. use a fixed-point `pow`/`ln`+`exp` approximation that operates on `exp` directly in BPS units instead of first reducing it via integer division, or require/validate that `curve-exponent` values are restricted to whole multiples of `BPS` if the simplified integer-power formula must be kept.

### Proof of Concept
1. DAO configures `curve-exponent = 15000` (intending `alpha = 1.5` for a concave graduated curve).
2. A position's `current-ltv` lands strictly between `ltv-liq-partial` and `ltv-liq-full`, giving `liq-pct-linear = factor` (some value < `BPS`, e.g. `5000` = 0.5).
3. `calc-liq-factor-exp(5000, 15000)` executes the `> exp BPS` branch: `(/ 15000 10000) = 1`, so the function returns `(pow 5000 1) / (pow 10000 0) = 5000` — identical to the *linear* (`alpha = 1`) result, instead of the intended `factor^1.5 ≈ 3536` (a smaller, more conservative value for a concave curve).
4. The inflated `liq-pct-scaled = 5000` (vs. intended ≈3536) is passed into `calc-liq-debt-repay` and `calc-liq-factor-bound`, producing a larger `max-debt-usd` and `liq-penalty` than the configured curve intends, letting the liquidator seize more of the borrower's collateral than designed.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L726-729)
```text
;; Calculate collateral to seize (includes liquidator bonus)
;; collateral-repay = debt-repay * (BPS + liq-penalty) / BPS
(define-private (calc-liq-collateral-repay (debt-repay uint) (liq-penalty uint)) 
  (mul-bps-down debt-repay (+ BPS liq-penalty)))
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
