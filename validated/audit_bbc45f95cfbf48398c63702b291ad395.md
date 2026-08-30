### Title
Interest compounds silently during vault `accrue` pause, causing a wrong LTV/health verdict and mass liquidation on unpause - (File: `mainnet/contracts/vault/v0-vault-usdc.clar` and equivalent asset vaults)

### Summary
The Zest v2 vault contracts (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdh.clar`) all implement a per-action pause mechanism, including an `accrue` flag inside `pause-states` that lets an admin freeze interest-index updates. When `accrue` is paused, `next-index`/`next-liquidity-index` simply return the current index unchanged, and `accrue` returns a pass-through result without advancing `last-update`. [1](#0-0)  This is exactly the pattern flagged in the BendDAO report: the timestamp used for interest computation is not reset/checkpointed at pause time, so once `accrue` is unpaused, the very next accrual call computes `time-delta = stacks-block-time - last-update` spanning the *entire* pause duration and compounds all of that missed interest into the index in a single step.

### Finding Description
`next-index` (identical logic across all vaults) computes the compounded multiplier using `last-update`, only skipping the calculation while `accrue` remains paused: [2](#0-1) 

The public `accrue` function only advances `last-update` when it actually changes the index, and while paused it takes the "pass-through" branch that neither updates the index nor `last-update`: [3](#0-2) 

Because `last-update` is left stale for the whole pause window, the very first `accrue()` call after unpausing (triggered automatically inside `system-borrow`/`system-repay`) computes `time-delta` including the full pause duration and applies compounded interest for that whole period in one jump, instead of spreading it out or skipping it as BendDAO's report recommended.

This inflated borrow index is consumed directly by the market's health/liquidation math. `v0-4-market.clar::liquidate` computes `current-ltv` from `total-debt-usd`, which is derived from the vault's borrow index via `get-cached-indexes`/`accrue-user-debts`, and immediately compares it against `ltv-liq-partial`/`ltv-liq-full`: [4](#0-3) 

The read-only health endpoint used by the front end/wiki (`get-user-position`) also derives `health-factor` from the same freshly-inflated `borrow-index`: [5](#0-4) [6](#0-5) 

So the LTV/health verdict computed right after unpausing is wrong in the sense that it reflects a step-function jump in debt that the borrower had no opportunity to react to or repay during the freeze, exactly mirroring the BendDAO scenario.

### Impact Explanation
Direction of error: debt is understated while `accrue` is paused, then suddenly and fully corrected (increased) in one shot the moment the pool/asset is unpaused, pushing `current-ltv` above `ltv-liq-partial`/`ltv-liq-full` for borrowers who were healthy (or only marginally under threshold) before the pause. This exposes borrowers to immediate liquidation with no chance to repay, transferring value to liquidators (who profit from the liquidation penalty/bonus, e.g. via `liq-penalty-min`/`liq-penalty-max` in `calc-liquidation-params`) at the expense of borrowers. This is a temporary/permanent freezing-of-funds style impact for the affected borrowers (forced loss of collateral beyond what would have accrued under normal, continuous interest), landing in the High impact category (temporary/permanent freezing of user funds due to unavoidable, un-mitigated liquidation caused by a timestamp bug rather than genuine market risk).

### Likelihood Explanation
`accrue` pausing is an emergency/admin-controlled action already present in all six mainnet vaults, and there is no code path that checkpoints `last-update` at pause time or resets/skips the frozen duration on unpause. Any real-world emergency pause of nontrivial length (which is the exact use case the pause flag exists for) will reproduce this compounding jump for every open borrow position in that vault, making the likelihood non-negligible whenever the admin needs to invoke the pause feature for actual emergencies.

### Recommendation
Persist the timestamp (or accumulated pause duration) at which `accrue` was paused, and on unpause either (a) reset `last-update` to the unpause time so no interest accrues for the paused interval, or (b) explicitly subtract the paused duration from `time-delta` in `next-index`/`next-liquidity-index` before computing the compounded multiplier, consistent with the mitigation BendDAO's judge required.

### Proof of Concept
1. Borrower opens a position with `current-ltv` just below `ltv-liq-partial` (e.g., 84% vs. 85% threshold) in `v0-4-market.clar`.
2. Protocol admin pauses `accrue` on the relevant vault (e.g., `v0-vault-usdc.clar`) for an extended emergency window (days/weeks) — `pause-states.accrue = true`, freezing `next-index` per [2](#0-1) , while `last-update` remains unchanged.
3. Admin unpauses (`pause-states.accrue = false`).
4. Any subsequent `system-borrow`/`system-repay`/`liquidate` call triggers `accrue()`, which computes `time-delta` over the entire pause duration and compounds it into `groupData`/`index` in one step per [3](#0-2) .
5. Borrower's debt jumps discontinuously; `current-ltv` in `liquidate` now exceeds `ltv-liq-partial`, satisfying the health check at [7](#0-6)  even though the borrower took no risky action — they are liquidated purely due to the pause-induced compounding jump.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L98-115)
```text
;; -- Pause states
(define-data-var pause-states
  {
    deposit: bool,
    redeem: bool,
    borrow: bool,
    repay: bool,
    accrue: bool,
    flashloan: bool
  }
  {
    deposit: false,
    redeem: false,
    borrow: false,
    repay: false,
    accrue: false,
    flashloan: false
  })
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L381-392)
```text
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
```

**File:** local-testing/contracts/vault/vault-usdh.clar (L841-865)
```text
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1422-1435)
```text
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
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L446-460)
```text
              ;; Calculate LTV
              (current-ltv (if (is-eq coll-usd u0)
                              (if (is-eq debt-usd u0) u0 BPS)
                              (mul-div-down debt-usd BPS coll-usd)))
              ;; Get egroup for health calculation
              (egroup-result (contract-call? .v0-egroup resolve mask)))
          (match egroup-result
            egroup
              (let ((ltv-borrow (buff-to-uint-be (get LTV-BORROW egroup)))
                    (ltv-liq-partial (buff-to-uint-be (get LTV-LIQ-PARTIAL egroup)))
                    ;; Health factor: (coll x ltv-borrow) / debt, scaled to BPS
                    ;; >10000 = healthy, <10000 = unhealthy
                    (health-factor (if (is-eq debt-usd u0)
                                      u100000000  ;; Infinite health if no debt
                                      (mul-div-down (mul-bps-down coll-usd ltv-borrow) BPS debt-usd))))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L490-507)
```text
(define-private (build-debt-entry (debt-entry { aid: uint, scaled: uint }))
  (let ((aid (get aid debt-entry))
        (scaled (get scaled debt-entry))
        (asset-status (unwrap-panic (contract-call? .v0-assets get-status aid)))
        (borrow-index (get-vault-borrow-index aid))
        ;; Calculate actual debt with compound interest
        (actual (mul-div-down scaled borrow-index INDEX-PRECISION))
        ;; Interest accrued = actual - scaled (simplified, assumes initial index ~= PRECISION)
        (interest (if (> actual scaled) (- actual scaled) u0)))
    {
      asset-id: aid,
      asset-addr: (get addr asset-status),
      underlying: (get addr asset-status),
      scaled-debt: scaled,
      borrow-index: borrow-index,
      actual-debt: actual,
      interest-accrued: interest
    }))
```
