### Title
Incorrect liquidation curve exponent math causes wrong liquidation penalty/percentage - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`calc-liq-factor-exp` is supposed to raise the linear liquidation factor to the configured `curve-exponent` power (`factor^alpha`, where `alpha = curve-exponent / BPS`) to produce a graduated liquidation curve. The implementation decomposes the exponent using integer division and a hard-coded square-root fallback, which only produces the mathematically correct result for a narrow set of exponent values (exact multiples of `BPS`, or exactly `BPS/2`). For any other configured `curve-exponent`, the computed liquidation percentage/penalty diverges from the intended curve, exactly analogous to the referenced ELO report where an exponent was incorrectly decomposed via an unjustified offset/root trick instead of computing the exponential directly.

### Finding Description
`calc-liq-factor-exp` in `mainnet/contracts/market/v0-4-market.clar`: [1](#0-0) 

```
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS) 
    factor
    (if (> exp BPS) 
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
```

This function is fed `curve-exponent` (an egroup-configured, DAO-settable liquidation curve parameter, BPS-scaled so `BPS`=1.0) and the linear liquidation factor from `calc-liq-factor`, then used to compute `liq-pct-scaled` inside `calc-liquidation-params`: [2](#0-1) 

Two independent math errors exist:

1. **Integer-division truncation for `exp > BPS`.** `(/ exp BPS)` is integer division. Any `curve-exponent` that is not an exact multiple of `BPS` (10000) loses its fractional part entirely before the exponentiation happens. E.g. `curve-exponent = 15000` (intended `alpha = 1.5`) truncates to `exp/BPS = 1`, so the code computes `pow(factor,1)/pow(BPS,0) = factor`, i.e. it silently behaves as `alpha = 1` (linear) instead of `alpha = 1.5` (superlinear). This is the same class of bug as the ELO report: the exponent is decomposed via an algebraic identity that is not compatible with the integer arithmetic actually performed, silently changing the effective exponent.

2. **Hard-coded `sqrti` for any `exp < BPS`.** For every `curve-exponent` strictly less than `BPS` (i.e. any sub-linear curve, `0 < alpha < 1`), the code unconditionally computes `sqrti(factor * BPS)`, i.e. `factor^0.5`, regardless of the actual configured value. A DAO-configured `curve-exponent` of, say, `3000` (`alpha=0.3`) or `9000` (`alpha=0.9`) is silently replaced by `alpha=0.5`. The comment itself ("assume factor^0.5") confirms the implementation is a fixed approximation, not a computation of the actual configured exponent — mirroring the ELO bug's incorrect exponent substitution.

The egroup registry and DAO init proposals define/allow arbitrary `curve-exponent` values as part of the graduated-liquidation design: [3](#0-2) 

The bug is not a misconfiguration by the DAO — it is the core math in `v0-4-market.clar` producing a wrong `liq-pct-scaled` for any legitimately-configured curve-exponent other than the few values that happen to round correctly (multiples of `BPS`, or exactly `BPS/2`).

`liq-pct-scaled` directly drives:
- `calc-liq-factor-bound` → `liq-penalty` (bonus paid to liquidator): [4](#0-3) 
- `calc-liq-debt-repay` → `max-debt-usd` (cap on how much debt can be liquidated in one call): [5](#0-4) 

### Impact Explanation
Because the truncated/approximated exponent produces a different `liq-pct-scaled` than intended:
- If the effective (buggy) exponent yields a *higher* `liq-pct-scaled` than the correctly configured curve would (e.g. truncation of `alpha=1.5` down to `alpha=1` when the underlying `factor < 1` makes `factor^1 > factor^1.5`), liquidators can force a larger liquidation percentage and larger `liq-penalty` bonus than the DAO intended, extracting excess value from borrowers being liquidated on every partial liquidation. This is a liquidator-side profit at the direct expense of the borrower's collateral — falls under theft/permanent loss of user funds during liquidation (Critical/High depending on magnitude, since it is a systematic, repeatable miscalculation on every liquidation using a non-trivial `curve-exponent`).
- If it yields a *lower* `liq-pct-scaled`, positions liquidate more slowly/partially than intended, which can leave under-collateralized debt unresolved for longer, increasing bad-debt/insolvency risk for the protocol.

Either direction is a wrong quantitative liquidation outcome stemming purely from in-contract math, not from oracle data or DAO misconfiguration, matching the "wrong price/wrong health-outcome via exponent conversion" analog class.

### Likelihood Explanation
Triggered any time an egroup is configured with a `curve-exponent` other than an exact multiple of `BPS` or exactly `BPS/2` — a very plausible configuration for "graduated liquidation," since the whole point of the feature is to support non-trivial curve shapes (e.g. 1.2x, 1.5x, 0.7x). No special attacker capability is required beyond triggering a normal liquidation on an egroup configured this way; a liquidator can also proactively choose to liquidate positions where the miscalculation benefits them most.

### Recommendation
Replace the integer-division-based exponent decomposition with a fixed-point power function that operates on the true rational exponent `curve-exponent / BPS` directly (e.g. a `pow-fixed`/`ln`+`exp` fixed-point implementation, or restrict `curve-exponent` at config-time to only the small set of values the current code can correctly evaluate and validate that constraint on-chain in the egroup setter). At minimum, remove the `sqrti`-as-`^0.5` fallback and the truncating `(/ exp BPS)` and implement `factor^(exp/BPS)` with proper fractional-exponent support.

### Proof of Concept
Given `factor = 0.8 * BPS = 8000` (linear liquidation factor of 80%) and `curve-exponent = 15000` (intended `alpha = 1.5`):

- Correct: `liq-pct = factor^1.5 = 0.8^1.5 ≈ 0.7155` → `liq-pct-scaled ≈ 7155`.
- Code: `exp/BPS = 15000/10000 = 1` (integer division) → `pow(factor,1)/pow(BPS,0) = 8000/1 = 8000` → `liq-pct-scaled = 8000` (i.e. exactly the *linear* factor, `alpha=1` instead of `1.5`).

This directly inflates `liq-pct-scaled` from 7155 to 8000, which via `calc-liq-factor-bound` and `calc-liq-debt-repay` increases both the liquidator's bonus (`liq-penalty`) and the maximum debt that can be liquidated in a single call, beyond what the DAO-configured 1.5x curve was meant to allow — a direct, reproducible mis-calculation with no oracle or DAO-config fault involved.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L715-719)
```text
;; Scale penalty between min and max using liquidation factor
;; liq-penalty = liq-penalty-min + (liq-factor * (liq-penalty-max - liq-penalty-min) / BPS)
;; Capped at bound-max to handle cases where liq-factor > BPS
(define-private (calc-liq-factor-bound (liq-factor uint) (bound-min uint) (bound-max uint))
  (min bound-max (+ bound-min (mul-bps-down liq-factor (- bound-max bound-min)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L721-724)
```text
;; Calculate debt to repay based on liquidation factor
;; debt-repay = liq-factor * debt / BPS
(define-private (calc-liq-debt-repay (debt uint) (liq-factor uint)) 
  (mul-bps-down liq-factor debt))
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

**File:** mainnet/contracts/proposals/mainnet/v0-init.clar (L1-1)
```text
(impl-trait .dao-traits.proposal-script)
```
