### Title
Dust-avoidance logic seizes 100% of collateral while `debt-to-repay` stays capped at the partial (graduated) liquidation amount - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
In `liquidate`, when the leftover ("remaining") slice of a user's collateral rounds down to zero repayable debt, the contract bumps the seized collateral amount up to the borrower's *entire* balance for that asset, but never correspondingly increases `debt-to-repay` (which is used both for the actual debt reduction and for the `min-collateral-expected`/event accounting). This is the same class of bug as the referenced Yield/Witch H-02 finding: a threshold/dust "round up to 100%" rule applied to only one side (collateral) of a liquidation, producing a mismatched, non-proportional seizure of collateral relative to debt actually repaid.

### Finding Description
The graduated liquidation math computes a proportional debt/collateral pair via `calc-liquidation-params` → `process-debt-asset` / `process-collateral-asset` → `calc-final-liquidation-amounts` → `scale-debt-for-liquidation`, yielding `debt-to-repay` and `coll-final-raw` that are proportional to `liq-pct-scaled` (the graduated liquidation percentage), not necessarily 100% of the position. [1](#0-0) 

The code then computes `coll-remaining = user-coll-balance - coll-final-raw` and estimates the debt value that this remainder would repay (`remaining-debt-to-repay`) using several rounding-down operations (`normalize(... false)`, `div-bps-down`, `mul-div-down` twice) followed by a single `mul-div-up`: [2](#0-1) 

If `remaining-debt-to-repay` rounds down to `u0` (which happens whenever the leftover collateral's USD-equivalent debt, after two down-rounding conversions, floors to zero scaled units), the code sets:
```
(coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw))
```
i.e. `coll-final` jumps from the proportional `coll-final-raw` to the *entire* `user-coll-balance` for that asset. However, `debt-to-repay` (computed earlier from `scaled-to-remove`, itself derived from the proportional `debt-final`) is never recalculated or increased to match this jump. [3](#0-2) 

The mismatched pair (`debt-to-repay`, `coll-final`) is then used directly to execute the liquidation: the debt reduction uses the small proportional `debt-to-repay`/`scaled-to-remove`, while the collateral transfer uses the inflated `coll-final` (= full balance): [4](#0-3) 

This exactly mirrors the reported bug class: a "if below threshold, escalate to 100%" rule is intended to apply consistently to both debt and collateral (as Yield's maintainer confirmed: *"If the remaining part of the vault is below dust, increase to 100%"* — meaning both sides), but here only the collateral side is escalated while the debt side is left at its partial/graduated value.

### Impact Explanation
The liquidator ends up seizing the borrower's full remaining collateral balance for the asset while only being required to repay the smaller, graduated `debt-to-repay` amount. This directly transfers excess collateral value from the borrower to the liquidator beyond what the graduated liquidation percentage (`liq-pct-scaled`) and liquidation penalty (`liq-penalty`) intend, i.e. direct theft of user (borrower) funds at rest. This lands in the Critical impact category (direct theft of user funds at rest).

### Likelihood Explanation
The trigger condition (`remaining-debt-to-repay` rounding to `u0`) depends only on token decimals, price, and `INDEX-PRECISION`/borrow-index scaling of the specific collateral/debt pair being liquidated near the tail of a graduated liquidation — no oracle manipulation or privileged access is required, and it can be hit by any liquidator simply choosing `debt-amount` such that `coll-remaining` falls into the rounding-to-zero range for that asset pair's decimals/price combination.

### Recommendation
When `remaining-debt-to-repay` rounds to zero and the code decides to sweep the full collateral balance (`coll-final = user-coll-balance`) to avoid leaving unliquidatable dust, `debt-to-repay`/`scaled-to-remove` must be increased symmetrically to reflect repayment of the full remaining debt for that asset (capped at `curr-scaled`), not left at the original proportional value. Alternatively, only escalate collateral seizure up to the amount that keeps `coll-final`/`debt-to-repay` proportional, and leave true dust remainders to a separate, bounded dust-sweep path rather than silently zeroing out the debt requirement for the escalated collateral.

### Proof of Concept
1. A position is graduated-liquidated with `liq-pct-scaled` well below 100% (e.g. LTV just above `ltv-liq-partial`), giving `debt-final`/`debt-to-repay` proportional to a small slice of the debt, and `coll-final-raw` proportional to a small slice of collateral.
2. `coll-remaining = user-coll-balance - coll-final-raw` is chosen (by the liquidator picking `debt-amount`) such that, after the down-rounding chain (`normalize`, `div-bps-down`, `mul-div-down` ×2), `rem-scaled` floors to `0`, making `remaining-debt-to-repay = u0`. [5](#0-4) 
3. `coll-final` is set to `user-coll-balance` (100% of the asset's collateral) while `debt-to-repay`/`scaled-to-remove` remain at the small graduated amount computed in step 1.
4. `vault-system-repay`/`debt-remove-scaled` reduce debt by only the small `debt-to-repay`, while `collateral-remove` transfers the borrower's entire collateral balance for that asset to the liquidator. [6](#0-5) 
5. The liquidator profits by receiving full collateral for a fraction of the intended debt repayment; the borrower loses the entire collateral position for that asset despite only a partial liquidation being warranted by the graduated LTV curve.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1470-1486)
```text
    ;; debt scaling for storage
    (curr-scaled (get-account-scaled-debt borrower debt-aid))
    (scaled-info (scale-debt-for-liquidation debt-final coll-actual curr-scaled debt-aid))
    (scaled-to-remove (get scaled-to-remove scaled-info))
    (debt-to-repay (get debt-to-repay scaled-info))
    (coll-final-raw (get coll-final scaled-info))
    (coll-remaining (- user-coll-balance coll-final-raw))
    (remaining-debt-to-repay
      (if (> coll-remaining u0)
        (let ((rem-coll-usd (normalize (* coll-remaining coll-price) coll-decimals false))
              (rem-debt-usd (div-bps-down rem-coll-usd (+ BPS liq-penalty-max)))
              (rem-debt-tokens (mul-div-down rem-debt-usd (pow u10 debt-decimals) debt-price))
              (rem-borrow-index (get index (unwrap-panic (get-cached-indexes debt-aid))))
              (rem-scaled (mul-div-down rem-debt-tokens INDEX-PRECISION rem-borrow-index)))
          (mul-div-up rem-scaled rem-borrow-index INDEX-PRECISION))
        u1))
    (coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1512)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))

    ;; update obligations and socialize bad debt
    (let ((debt-updated (try! (contract-call? .v0-market-vault
                              debt-remove-scaled
                              borrower
                              scaled-to-remove
                              debt-aid)))
          ;; Collateral receiver defaults to liquidator if not specified
          (actual-receiver (match collateral-receiver recv recv liquidator))
          (coll-removed (try! (contract-call? .v0-market-vault
                              collateral-remove
                              borrower
                              coll-final
                              collateral-ft
                              coll-aid
                              actual-receiver)))
```
