Confirmed: the `set-pause-states` function found identically across `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, and `v0-vault-usdh.clar` contains the exact bug class from the Furnace report: on unpausing `accrue`, `last-update` is jumped forward to the current block time, discarding the interest/liquidity-index growth that borrowers/lenders should have accrued for the entire paused interval, even though on-pause the code *does* call `accrue()` first to snapshot pending interest up to that point.

### Title
Jumping `last-update` on unpause permanently discards interest accrual for the paused period, understating the debt/liquidity index and mispricing zTokens - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
When the DAO unpauses accrual via `set-pause-states`, the vault sets `last-update` to `stacks-block-time` instead of leaving it at the timestamp it had when the pause began. This mirrors the Furnace mitigation bug: instead of correctly rolling forward the accounting period so that interest for the *entire* elapsed time (including the paused interval) is captured, the code simply erases that interval from the interest-rate time-delta calculation, so borrowers are never charged, and lenders never credited, interest for the time the vault was paused.

### Finding Description
`next-index` / `next-liquidity-index` compute `time-delta` as `(- stacks-block-time (var-get last-update))` and use it to grow the debt `index` and liquidity `lindex` via `calc-multiplier-delta`, e.g. [1](#0-0) . The `accrue` function only advances `last-update` when the computed index actually changes and the vault is not paused, e.g. [2](#0-1) .

`set-pause-states` explicitly special-cases the transition out of pause: [3](#0-2) . On pausing accrual it correctly calls `accrue()` first to snapshot pending interest up to that point (comment: "When pausing accrue, accrue first to capture pending interest"). But on unpausing, instead of leaving `last-update` untouched (so the next `accrue()` call would correctly compute `time-delta` spanning the whole paused interval and apply it at the current rate), the code does `(var-set last-update stacks-block-time)` — i.e., it manually resets the accrual clock to "now," permanently erasing the paused interval from all future `time-delta` computations. Any interest that should have accrued during the pause (per the DAO-configured interest rate curve, `interest-rate`/`points-ir`) is silently dropped rather than being applied when accrual resumes.

This is the same problem highlighted in the external report on `Furnace.sol`: instead of ensuring a skipped/failed period is caught up correctly, the code updates the "last update" checkpoint in a way that guarantees the corresponding value (melting there / interest accrual here) for that period is permanently lost.

### Impact Explanation
The debt `index` (via `next-index`) and liquidity `lindex` (via `next-liquidity-index`) directly drive `total-debt`, `total-assets`, and (via `resolve-ztoken` in the market's oracle resolution) the price of every zToken: [4](#0-3) . Because `lindex` never grows for the paused duration, zToken holders permanently lose the yield they should have earned over that interval — this is an unclaimed-yield freezing/loss, and simultaneously borrowers escape interest they should owe for the same period, meaning the protocol/treasury also permanently loses the corresponding reserve-fee cut (`reserve-inc`/`treasury-lp`) that would have been minted from that interest, as seen in `accrue`: [5](#0-4) . This lands in the "permanent freezing/loss of unclaimed yield" impact category.

### Likelihood Explanation
Pausing/unpausing `accrue` is a normal, expected DAO operational lever (e.g., during maintenance, oracle issues, or incident response), not an edge case; every pause/unpause cycle on any of the six vaults (`v0-vault-stx`, `v0-vault-sbtc`, `v0-vault-ststx`, `v0-vault-ststxbtc`, `v0-vault-usdc`, `v0-vault-usdh`) triggers this loss deterministically and silently, with no error or event indicating value was dropped.

### Recommendation
On unpausing accrual, do not reset `last-update` to `stacks-block-time`. Instead, leave `last-update` at its pre-pause value (or explicitly call `accrue()` immediately after flipping the pause flag) so the next accrual correctly computes `time-delta` across the full paused interval and applies the current interest rate retroactively to that entire span, consistent with how the pre-pause `accrue()` call already captures interest up to the pause point.

### Proof of Concept
1. DAO calls `set-pause-states` with `accrue: true` — vault calls `accrue()`, snapshotting `index`/`lindex` and `last-update` at time T0.
2. Time passes to T1 (e.g., 30 days) while `accrue: true` (paused); utilization and interest continue to exist conceptually via `interest-rate`, but no accrual happens because `next-index`/`next-liquidity-index` short-circuit to the stored `idx`/`lidx` while paused: [6](#0-5) .
3. DAO calls `set-pause-states` with `accrue: false` at T1 — the code executes `(var-set last-update stacks-block-time)`, setting `last-update = T1`.
4. Any subsequent `accrue()` call computes `time-delta = (current-time - T1)`, never including the T0→T1 interval, so the 30 days of interest that should have accrued to lenders (and been owed by borrowers) is permanently skipped from `index` and `lindex`, and the corresponding treasury reserve fee is never minted.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-390)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L723-748)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
      
      (print {
        action: "vault-set-pause-states",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          states: states
        }
      })
      
      (ok true))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L841-850)
```text
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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L858-863)
```text
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
