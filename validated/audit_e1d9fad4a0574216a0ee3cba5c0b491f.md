### Title
Liquidation curve exponent `calc-liq-factor-exp` silently truncates fractional exponents and hardcodes a wrong power for any sub-1.0 value - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`calc-liq-factor-exp` is the Cobb-Douglas-style exponentiation step of the graduated liquidation formula (`factor^alpha`, where `alpha = curve-exponent / BPS`). Just like the Venus `Scores.sol` bug, the implementation special-cases only a few discrete values of the exponent (exactly `1.0`, integer multiples `>1.0`) and, for the entire remaining continuous range `(0, BPS)`, hardcodes the result to `sqrt(factor)` (i.e. always assumes `alpha = 0.5`), regardless of the actual configured `curve-exponent`.

### Finding Description
The function is:
```
(define-private (calc-liq-factor-exp (factor uint) (exp uint))
  (if (is-eq exp BPS) 
    factor
    (if (> exp BPS) 
        (/ (pow factor (/ exp BPS)) (pow BPS (- (/ exp BPS) u1)))
        (sqrti (* factor BPS))))) ;; assume factor^0.5
``` [1](#0-0) 

- When `exp == BPS` (alpha = 1.0), it correctly returns `factor` unchanged.
- When `exp > BPS`, it performs **integer division** `(/ exp BPS)` to derive the exponent, silently truncating any non-integer multiple (e.g. `exp = 15000` → alpha should be 1.5, but `(/ 15000 10000)` = `1`, so the whole branch collapses to `pow factor 1 / pow BPS 0` = `factor`, identical to alpha = 1.0, even though the LTV-registered curve exponent says 1.5).
- When `0 < exp < BPS` (any alpha strictly between 0 and 1, e.g. 0.3, 0.7, 0.9), the code does not compute `factor^alpha` at all — it always returns `sqrti(factor * BPS)`, i.e. hardcoded `factor^0.5`, comment literally says `;; assume factor^0.5`.

This is used directly in the liquidation flow: `calc-liquidation-params` calls `calc-liq-factor-exp` on the linear liquidation factor with the egroup's `LIQ-CURVE-EXP`, producing `liq-pct-scaled`, which is then used to compute both the liquidation penalty (`calc-liq-factor-bound`) and the max debt repayable (`calc-liq-debt-repay`): [2](#0-1) 

`LIQ-CURVE-EXP` is a DAO-configured, buffer-read field from the egroup registry (`buff-to-uint-be (get LIQ-CURVE-EXP group)`), used inside `liquidate`: [3](#0-2) 

So for any egroup configured with a curve exponent that is not exactly `10000` (1.0) or an exact integer multiple of `10000` (2.0, 3.0, ...), the actual liquidation percentage and penalty computed on-chain will diverge from what the DAO intended when setting that parameter. This mirrors the Venus bug's root cause: an approximation/special-casing of the exponent function that only handles boundary/whole values correctly, producing systematically wrong output for the general case that the parameter is documented to support.

### Impact Explanation
`liq-pct-scaled` directly drives `calc-liq-debt-repay` (max debt a liquidator may repay) and `calc-liq-factor-bound` (liquidation bonus/penalty). If the actual configured exponent is misapplied (e.g., a DAO sets `curve-exponent = 3000` for a mild penalty curve, but the code always applies `factor^0.5` instead of `factor^0.3`), the liquidation percentage and penalty paid to liquidators/borrowers will be computed with the wrong curve. Depending on direction, this either:
- Over-liquidates positions / over-penalizes borrowers (temporary freezing/loss of borrower collateral beyond intended amount), or
- Under-liquidates positions relative to intended risk parameters, leaving under-collateralized debt unresolved for longer, risking protocol insolvency in tail scenarios.

This lands on temporary freezing of funds (excess collateral seized from borrowers beyond the intended curve) and, in the under-liquidation direction, contributes to protocol insolvency risk since the liquidation incentive/amount no longer matches the risk curve the DAO configured.

### Likelihood Explanation
Likelihood depends entirely on whether any egroup is (or will be) configured with a `LIQ-CURVE-EXP` value that is not exactly `10000` or an exact multiple of `10000`. Sample proposals in the repo configure `LIQ-CURVE-EXP` at `u20000` (2.0, which happens to still work correctly since `20000/10000=2` exactly), so I could not find, within the indexed files, a currently-deployed egroup with a genuinely fractional/sub-1.0 exponent that would trigger the incorrect `sqrti` branch or the truncation branch. This is a latent correctness bug in the general formula rather than a demonstrated live misconfiguration; likelihood is contingent on future DAO parameter choices exercising the un-handled input space, which the code should support per its own design (a continuous curve exponent, not just discrete presets).

### Recommendation
Implement a proper fractional power function (e.g., fixed-point `pow` via `ln`/`exp` or a binomial/Newton approximation for rational exponents) so that `calc-liq-factor-exp` correctly computes `factor^(exp/BPS)` for the full continuous range `(0, +inf)` rather than only exact integer or the single hardcoded `0.5` case. At minimum, add a registry-level `asserts!` in `egroup.clar`'s insert function restricting `LIQ-CURVE-EXP` to only the values actually supported by `calc-liq-factor-exp` (i.e., `BPS`, or exact multiples of `BPS`, or exactly `BPS/2`), and document this restriction, to avoid silent mismatches between configured intent and executed math.

### Proof of Concept
Given `factor = 4000` (40% linear liquidation factor, in BPS) and a DAO-configured `curve-exponent = 3000` (intending alpha = 0.3, a curve that liquidates more gently near partial threshold than the 0.5 default):

- Expected (per the documented formula `factor^alpha`): `4000^0.3` (scaled) ≈ a value distinct from `sqrt(4000 * 10000)`.
- Actual code path: since `0 < 3000 < BPS(10000)`, the `else` branch always executes `(sqrti (* factor BPS))` = `sqrti(4000 * 10000)` = `sqrti(40000000)` ≈ `6324`, which is `factor^0.5`, not `factor^0.3`.
- The DAO's intended curve exponent (`0.3`) is silently discarded; the protocol behaves as if `curve-exponent` were always `5000` for any value in `(0, 10000)`. Similarly, `curve-exponent = 15000` (alpha=1.5) collapses via integer division to alpha=1.0, producing `liq-pct-scaled = factor` unchanged instead of the intended super-linear curve. [1](#0-0)

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

**File:** mainnet/contracts/market/v0-4-market.clar (L739-751)
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
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1416-1420)
```text
    (ltv-liq-partial (buff-to-uint-be (get LTV-LIQ-PARTIAL group)))
    (ltv-liq-full (buff-to-uint-be (get LTV-LIQ-FULL group)))
    (liq-penalty-min (buff-to-uint-be (get LIQ-PENALTY-MIN group)))
    (liq-penalty-max (buff-to-uint-be (get LIQ-PENALTY-MAX group)))
    (curve-exponent (buff-to-uint-be (get LIQ-CURVE-EXP group)))
```
