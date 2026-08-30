### Title
Missing minimum-shares-minted check in `deposit` allows depositors to receive zero shares for a nonzero deposit - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and sibling vaults `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`)

### Summary
Every Zest v0 vault computes shares to mint on deposit as `inkind = (amount * total-supply-preview) / total-assets-preview`, using `mul-div-down` (round-down division). Unlike `redeem`, which explicitly guards against a zero output with `ERR-OUTPUT-ZERO`, `deposit` has no equivalent guard on `inkind`, relying only on the caller-supplied `min-out` slippage parameter.

### Finding Description
`convert-to-shares-preview` computes: [1](#0-0) 
using round-down `mul-div-down`. As the vault's price-per-share (`total-assets-preview / total-supply-preview`) grows over time from accrued borrower interest, `amount * ts / ta` can round down to `0` for small deposit amounts relative to the current share price.

`deposit` uses this value directly to mint shares and to debit the `min-out` slippage check, but never asserts `inkind > u0`: [2](#0-1) 

Compare this to `redeem`, in the very same contract, which does enforce a non-zero output: [3](#0-2) 

If a caller supplies `min-out = 0` (the natural default for a "no slippage protection needed" deposit, or simply an unaware integrator/UI), the `ERR-SLIPPAGE` check `(>= inkind min-out)` at line 776 passes trivially when `inkind` is `0`. The deposit then proceeds: `receive-underlying` pulls the full `amount` of the underlying asset from the depositor, `ft-mint?` mints `0` shares to `recipient`, and `assets` is still increased by the full `amount`: [4](#0-3) 

This is identical in every one of the six vault contracts (`v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`), which all share the same `convert-to-shares-preview` / `deposit` pattern.

Note: unlike the original Lido-style report, Zest's `total-assets` is derived from the internal `var-get assets` accounting variable plus accrued interest, not from a live balance query of the underlying token (`ubalance`) — so the classic "self-destruct wei donation" front-running vector to artificially inflate `total-assets` is not directly reachable here. The exploitable root cause in Zest is narrower but still real: the price-per-share naturally grows from borrower interest accrual over the vault's life, and the missing zero-shares guard on `deposit` means any depositor whose contribution rounds down to zero shares at the prevailing price silently forfeits their underlying deposit to existing shareholders, with no revert.

### Impact Explanation
The depositor's underlying asset (`amount`) is transferred into the vault and added to `assets`, but they receive `0` vault shares (`zft`) in return, permanently losing access to that value — it is redistributed pro-rata to existing shareholders by increasing the shares' redeemable value. This fits "permanent freezing of funds" / "theft of unclaimed yield" for the affected depositor's contribution, and is a direct value transfer from a new depositor to existing shareholders.

### Likelihood Explanation
This requires: (1) the vault's share price (`total-assets-preview / total-supply-preview`) to have grown enough (via accrued borrower interest, which happens naturally over time without needing DAO or oracle misconfiguration) that a small deposit amount rounds to zero shares under `mul-div-down`, and (2) the caller/integrator to pass `min-out = 0` or otherwise fail to bound the expected shares. Both conditions are plausible in production usage (e.g., a UI/integration that doesn't compute a nonzero `min-out`, or dust deposits), making this a realistic, low-cost-to-trigger issue rather than a purely theoretical one.

### Recommendation
Add an explicit `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` check in `deposit`, mirroring the existing guard already present in `redeem`, across all six vault contracts (`v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`).

### Proof of Concept
1. Let the vault run long enough (or simulate via `accrue`) so that `total-assets-preview / total-supply-preview` (price per share) exceeds `1e ` such that for a given small `amount`, `mul-div-down(amount, total-supply-preview, total-assets-preview)` rounds to `0`.
2. A depositor (or an integrator on their behalf) calls `deposit` with that `amount` and `min-out = 0`.
3. `inkind` (line 770) evaluates to `0`; `(>= inkind min-out)` at line 776 is `(>= 0 0)` = true, so `ERR-SLIPPAGE` does not trigger.
4. `receive-underlying` pulls `amount` from the depositor (line 779); `ft-mint?` mints `0` shares to `recipient` (line 780); `assets` increases by `amount` (line 781).
5. The depositor's transaction succeeds (`ok inkind` returns `ok 0`), but they hold zero shares and cannot redeem any value for the `amount` they contributed.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L308-315)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-781)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L806-813)
```text
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)
```
