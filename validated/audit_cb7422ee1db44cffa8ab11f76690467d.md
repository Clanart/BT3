### Title
Liquidation dust-sweep rounds `remaining-debt-to-repay` to zero, letting the liquidator seize 100% of collateral while only paying for a partial debt repayment - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate` in `v0-4-market.clar` computes a partial liquidation amount (`coll-final-raw`, `debt-to-repay`) via the graduated liquidation curve, then checks whether the *leftover* collateral (`coll-remaining`) is worth sweeping entirely to the liquidator. That "worth sweeping" check (`remaining-debt-to-repay`) is derived through three chained round-down conversions and can legitimately compute to `u0` even when `coll-remaining` still has real USD value. When it does, the code sets `coll-final` to the borrower's **full** collateral balance for that asset — but the debt actually removed from the borrower (`scaled-to-remove`) and the amount actually repaid to the vault (`debt-to-repay`) are still only the *partial* amounts computed before the sweep. This is the same bug class as the external report: a saturating "give the rest away for free when the residual computes to zero" pattern caused by rounding-down chains, and it lets one party (the liquidator) receive value it never paid for.

### Finding Description
In `liquidate`: [1](#0-0) 

- `scaled-to-remove` / `debt-to-repay` / `coll-final-raw` are computed by `scale-debt-for-liquidation` strictly from the graduated `debt-final` amount — i.e. the *partial* liquidation slice.
- `coll-remaining = user-coll-balance - coll-final-raw` is the collateral that would be *left behind* after this partial liquidation.
- `remaining-debt-to-repay` estimates how much debt the leftover collateral is "worth", via `normalize(...) -> div-bps-down -> mul-div-down -> mul-div-down -> mul-div-up`. Every intermediate step except the last rounds down.
- If that chain rounds to `u0` (e.g. because `coll-remaining` is denominated in a low-value/high-decimal collateral like STX while `debt-price`/`debt-decimals` for the debt asset make fractional-token amounts round to zero), the code sets:
  `(coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw))`
  i.e. it swaps in the **entire** `user-coll-balance` as the amount to seize.
- However, `debt-to-repay` sent to `vault-system-repay` and `scaled-to-remove` sent to `debt-remove-scaled` are **not** correspondingly increased — they remain the partial values computed for `coll-final-raw`: [2](#0-1) 

The result: the liquidator repays debt only for the partial slice (`debt-to-repay`), but is credited the borrower's *full* collateral balance (`coll-final`) for that asset via `collateral-remove`. The gap in value — `coll-remaining` priced in USD — is transferred to the liquidator for free, at the borrower's expense (and ultimately the protocol's, since the borrower's remaining debt on other assets is left backed by less collateral, increasing bad-debt-socialization risk downstream).

This mirrors the reported bug class exactly: a computed "remainder" (`remaining-debt-to-repay`, analogous to `notional = fillAmount - notionalSoFar`) is driven to zero by chained rounding, and the zero-check flips a code path that hands out value (`coll-final = user-coll-balance`, analogous to the taker paying zero and still receiving `fillAmount`) without requiring the corresponding payment.

### Impact Explanation
This is direct theft of user (borrower) funds at rest: the borrower's collateral for the liquidated asset is fully seized while only a fraction of the corresponding debt is repaid/removed. The liquidator profits by the USD value of `coll-remaining` that should not have been part of this liquidation slice. Because the shortfall is not compensated anywhere (the vault only receives `debt-to-repay`), this also increases the likelihood of the borrower's remaining debt (on other collateral/other assets) becoming under-collateralized bad debt that must be socialized by the protocol — i.e., protocol insolvency exposure in addition to the direct borrower loss.

### Likelihood Explanation
This does not require a malicious operator, oracle manipulation, or DAO compromise — any liquidator can trigger it by choosing a `debt-amount` (subject to the existing partial-liquidation caps) such that the resulting `coll-remaining` for a collateral asset with unfavorable decimals/price relative to the debt asset rounds `remaining-debt-to-repay` to zero. Liquidators are economically incentivized to search for and exploit exactly this condition, since it yields free collateral on every qualifying liquidation call.

### Recommendation
Do not let the "sweep leftover collateral" branch increase `coll-final` beyond `coll-final-raw` without also increasing `debt-to-repay`/`scaled-to-remove` proportionally. Either:
1. Only trigger the full sweep when `coll-remaining` is below a fixed, price-independent dust threshold (not derived from a chain of round-downs that can zero out legitimate value), or
2. When sweeping the full `user-coll-balance`, also recompute and repay the debt that corresponds to the *entire* `user-coll-balance` (not just the partial `debt-final` slice), ensuring collateral seized and debt repaid stay in lockstep.

### Proof of Concept
Conceptual trace (no test harness available in this index — a Devin session with the full repo/test suite would be needed to produce a runnable PoC):
1. Borrower has a liquidatable position with collateral in an asset with many decimals/low unit price (e.g. STX) and debt in an asset with few decimals/high unit price (e.g. sBTC).
2. Liquidator calls `liquidate` with a `debt-amount` that results in a graduated `coll-final-raw` slightly less than `user-coll-balance`, so `coll-remaining > 0` but small in raw token terms.
3. `rem-coll-usd -> rem-debt-usd -> rem-debt-tokens` rounds down to `u0` sBTC (a fractional-token amount that truncates to zero given sBTC's decimals and price), making `remaining-debt-to-repay = u0`.
4. `coll-final` is set to `user-coll-balance` (full sweep), while `debt-to-repay`/`scaled-to-remove` remain the earlier partial values.
5. Liquidator receives `user-coll-balance` STX but only repaid debt for `coll-final-raw`'s worth — pocketing the USD value of `coll-remaining` for free. [3](#0-2) [2](#0-1)

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
