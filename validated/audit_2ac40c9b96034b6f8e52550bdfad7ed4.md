### Title
Dust-triggered full-collateral sweep during partial liquidation lets a liquidator seize the entire remaining collateral without paying for it - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The `liquidate` function computes a "sweep" override that grants the liquidator the borrower's *entire* remaining collateral balance whenever the USD value of the leftover (unseized) collateral, after three sequential floor-divisions, rounds down to zero implied debt tokens. Because the liquidator freely chooses `debt-amount` (and therefore how much collateral is left over as `coll-remaining`), they can deliberately size their liquidation so the leftover collateral lands in this rounding-to-zero dust band, causing the contract to hand them the full collateral balance while `debt-to-repay` remains based only on the smaller amount they actually paid for.

### Finding Description
In `liquidate`, after computing the primary seizure amount `coll-final-raw` (via `scale-debt-for-liquidation`), the contract computes how much of the borrower's collateral is left unseized: [1](#0-0) 

```
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

`rem-coll-usd`, `rem-debt-usd`, and `rem-scaled` are each computed with `normalize(..., false)`, `div-bps-down`, and `mul-div-down` — three consecutive floor divisions. Only the very last step (`mul-div-up`) rounds up, but rounding up `0` still yields `0`. If `coll-remaining` (the unseized portion of the borrower's collateral) is small enough that these successive floor-divisions collapse it to `0` implied debt tokens, `remaining-debt-to-repay` becomes `u0`, and the code substitutes `coll-final = user-coll-balance` — i.e. the liquidator is credited with the borrower's *entire* collateral balance for that asset, not just `coll-final-raw`.

Critically, `debt-to-repay` (the amount the liquidator actually pays, computed earlier from `debt-final`/`scaled-to-remove`) is **not** recomputed or increased to match this larger collateral seizure. `debt-amount` is an attacker-controlled input to `liquidate`, so a liquidator can select it precisely so that `coll-final-raw` (which depends on `debt-actual`, `liq-penalty`, and prices) leaves a `coll-remaining` value that lands in the zero-rounding band — collecting the full collateral balance while only paying for the smaller, deliberately undersized liquidation they submitted.

### Impact Explanation
This lets a liquidator obtain collateral tokens beyond what they paid debt for — a direct theft of a portion of the borrower's collateral (funds at rest) that were not part of the liquidation the liquidator actually funded. This falls under **Critical — direct theft of user funds at rest**, since the liquidator receives real collateral without a corresponding real debt repayment for the swept residual amount.

### Likelihood Explanation
`debt-amount` and `min-collateral-expected` are fully attacker-controlled parameters of the public `liquidate` entrypoint [2](#0-1) , so any account can trigger this path against any already-liquidatable position by choosing an appropriately small `debt-amount`, without needing any special privileges, oracle manipulation, or DAO/registry changes. The three-fold successive floor-division nature of the "remaining" calculation makes hitting the zero-rounding band achievable with realistic token decimals/prices (particularly for low-decimal or high-unit-price collateral), making this practically exploitable, not merely theoretical.

### Recommendation
- Do not use "implied debt of the remainder rounds to zero" as the trigger for sweeping the entire remaining collateral to the liquidator. Instead, only sweep dust when the *borrower's total remaining position* (across all debt, not just this single liquidation call) is confirmed dust, or cap the incremental collateral swept to a bounded, protocol-defined dust threshold that isn't attacker-selectable via `debt-amount`.
- Alternatively, require that when `remaining-debt-to-repay` collapses to zero, the liquidator's `debt-to-repay`/scaled-debt removal must also be adjusted to account for and consume the full remaining debt (full liquidation), not just the amount corresponding to the smaller `debt-amount` they supplied.
- Add a minimum-size/dust-threshold guard on the `coll-remaining` USD value independent of the liquidator-chosen `debt-amount`, so the sweep path cannot be deliberately triggered by undersizing the liquidation input.

### Proof of Concept
1. Borrower has a liquidatable position with collateral `user-coll-balance` in a low-decimal/high-unit-price asset (e.g., sBTC, 8 decimals, ~$60,000/unit).
2. Liquidator calls `liquidate` with a `debt-amount` deliberately smaller than the position's `max-debt-usd`, chosen so that `coll-final-raw` (computed by `process-collateral-asset` → `scale-debt-for-liquidation`) leaves `coll-remaining = user-coll-balance - coll-final-raw` at a small residual value.
3. Because `rem-coll-usd → rem-debt-usd → rem-scaled` are all floor-divided (`normalize(..., false)`, `div-bps-down`, `mul-div-down`), a sufficiently small `coll-remaining` produces `rem-scaled = 0`, hence `remaining-debt-to-repay = 0` (line 1484: `mul-div-up` of `0` is `0`).
4. `coll-final` is then set to `user-coll-balance` (line 1486) — the liquidator seizes 100% of the borrower's collateral for that asset — while `debt-to-repay` used in `vault-system-repay` (line 1496) is only the amount corresponding to the smaller, deliberately chosen `debt-amount`.
5. The liquidator profits by the value of the "extra" collateral swept beyond `coll-final-raw`, paid for by no additional debt repayment.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1390-1403)
```text
  (let (
    (feeds-check (try! (write-feeds price-feeds)))
    (liquidator contract-caller)
    (position (try! (get-liquidation-position borrower)))
    (pos-full (try! (get-full-position borrower)))
    (mask (get mask position))
    (group (try! (get-egroup mask)))

    (coll-address (contract-of collateral-ft))
    (debt-address (contract-of debt-ft))
    (coll-asset (try! (get-asset coll-address)))
    (debt-asset (try! (get-asset debt-address)))
    (coll-aid (get id coll-asset))
    (debt-aid (get id debt-asset))
```

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
