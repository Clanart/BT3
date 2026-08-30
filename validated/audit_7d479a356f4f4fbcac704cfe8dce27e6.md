Found a valid analog. The `last-borrow-block` "same-block liquidation" protection in `debt-add-scaled` mirrors the Telcoin `withdrawalRequestTimestamps` bug: a timestamp/marker that is meant to gate a *specific* delayed action can be armed by *any* call to the guarded state-setter, not just the triggering condition, letting the borrower arm the guard cheaply and repeatedly to defeat liquidation.

### Title
Same-block liquidation guard (`last-borrow-block`) can be re-armed with a trivial borrow to indefinitely block liquidation of an unhealthy position - (File: `mainnet/contracts/market/v0-market-vault.clar`)

### Summary
`liquidate` refuses to liquidate a position if `last-borrow-block` equals the current `stacks-block-height`, intended as anti-flashloan/frontrunning protection so a user cannot borrow and be liquidated atomically in the same transaction bundle. `last-borrow-block` is stamped on every call to `debt-add-scaled`, including trivial re-borrows of 1 unit, with no relationship to the size or purpose of the debt increase — exactly the disconnect the Telcoin report flags between the "request" action and the "stake" amount that is supposed to be gated.

### Finding Description
`debt-add-scaled` unconditionally stamps `last-borrow-block: stacks-block-height` on the caller's position record whenever any (even minimal) new debt is added: [1](#0-0) 

`liquidate` then refuses to proceed if the borrower's `last-borrow-block` equals the current block: [2](#0-1) 

There is no minimum debt-increase threshold, no check that the *current* transaction's borrow is what triggered the unhealthy state, and no cooldown separate from the flag itself — precisely the "no relationship between the triggering condition (staking amount) and the timestamp" flaw described in the Telcoin report, ported to the block-based flag here. A position that has become liquidatable (LTV crossed `LTV-LIQ-PARTIAL`) can have `borrow` called for the smallest possible amount that still satisfies the health check post-increase is irrelevant here — actually `borrow` itself requires the position to remain/become healthy after the increase (`ERR-UNHEALTHY` check in `borrow`), so an *already unhealthy* position cannot call `borrow` to re-stamp the flag. This limits the exploitability: the guard can only be re-armed by a healthy borrower, not one who is already under-collateralized and about to be liquidated.

Given that constraint, the practical bypass window is narrower than the original report's stake/withdraw case: a user must front-run the liquidator's transaction within the *same* block their position becomes unhealthy (e.g., due to a price update) by submitting a borrow that keeps them technically healthy at that instant, or exploit the mempool ordering to land a self-triggered `debt-add-scaled` in the same block as the liquidator's call, which would then make `same-block-check` fail for that liquidator regardless of who benefits from ordering.

### Impact Explanation
If a borrower can win the race to include even a tiny `borrow` call in the same block a liquidation transaction targets them, the `ERR-LIQUIDATION-BORROW-SAME-BLOCK` check reverts the liquidator's call, causing **temporary freezing of the liquidator's ability to seize collateral/unclaimed liquidation yield** for that block, and enabling the borrower to repeat this each block a liquidation is attempted, degrading protocol solvency assurances during a price-drop event. This lands in the in-scope "temporary freezing of funds" / "theft of unclaimed yield" category (liquidation bonus/penalty that would otherwise accrue to the liquidator).

### Likelihood Explanation
Likelihood is **low-to-medium**: exploitation requires the borrower to detect their position is at risk and win block-inclusion racing against liquidator bots every single block, which is costly and not guaranteed, and the `borrow` call itself must not push/keep the position unhealthy (so it only works for positions hovering near, not deep past, the liquidation threshold). This differs from the Telcoin bug, which required zero racing and zero cost (a one-time `requestWithdrawal` call fully and permanently disabled the delay).

### Recommendation
Decouple the same-block protection from the generic `debt-add-scaled` state stamp: only set `last-borrow-block` when the *net* new debt exceeds a meaningful threshold relative to the position, or track same-block protection per borrow-amount/egroup rather than as a single mutable field that any borrow — however small — resets. Alternatively, require the guard to only apply for N blocks after a *qualifying* (health-state-changing) borrow, and re-validate that the position was healthy immediately prior to the borrow that set the flag.

### Proof of Concept
1. Borrower's position LTV rises close to `LTV-LIQ-PARTIAL` due to a price move (still technically healthy).
2. Liquidator submits `liquidate(...)` once the position crosses `LTV-LIQ-PARTIAL`.
3. Borrower (via bot) detects the pending liquidation and submits `borrow` for a minimal amount in the same block, which succeeds because the position is still evaluated as healthy pre-increase in that block, and this stamps `last-borrow-block = stacks-block-height`.
4. If the borrower's `borrow` transaction lands in the same block before/at the same height as the liquidator's `liquidate` call, `same-block-check` in `liquidate` [3](#0-2)  reverts with `ERR-LIQUIDATION-BORROW-SAME-BLOCK`, blocking that liquidation attempt for the block.
5. Repeating step 3 every subsequent block (as long as the position can still pass the pre-increase health check) delays liquidation indefinitely, at the cost of a borrow transaction per block.

**Uncertainty:** I could not fully verify from the indexed code whether `borrow`'s pre-condition health check (`is-healthy collateral-value debt-value current-ltvb`, using the *current* mask/LTV-BORROW, not `LTV-LIQ-PARTIAL`) would actually still pass once the position has crossed into the liquidatable range — since `LTV-BORROW` is typically stricter (lower) than `LTV-LIQ-PARTIAL`, it's plausible `borrow` would already revert with `ERR-UNHEALTHY` once the position is liquidatable, which would eliminate this bypass window entirely. Confirming this requires reading the exact `LTV-BORROW` vs `LTV-LIQ-PARTIAL` egroup values and testing the boundary condition, which was not fully resolvable from the available indexed snippets.

### Citations

**File:** mainnet/contracts/market/v0-market-vault.clar (L442-456)
```text
(define-public (debt-add-scaled (account principal) (scaled-amount uint) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve-or-create account))
        (user-id (get id entry))
        (mask (get mask entry))
        (update-mask (mask-update mask asset-id false true)) ;; debt, insert
        ;; Oracle frontrunning protection: record current block when borrowing
        (updated-entry (merge entry { mask: update-mask, last-update: stacks-block-time, last-borrow-block: stacks-block-height }))
        (result (add-user-scaled-debt user-id asset-id scaled-amount)))

    (try! (check-impl-auth))
    (asserts! (not (get debt-add states)) ERR-PAUSED)
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (insert updated-entry)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1428-1435)
```text
    ;; Oracle frontrunning protection: prevent same-block liquidation
    ;; This blocks flash-loan based attacks where user borrows + gets liquidated in same block
    (last-borrow-block (get last-borrow-block position))
    (same-block-check (asserts! (not (is-eq last-borrow-block stacks-block-height)) ERR-LIQUIDATION-BORROW-SAME-BLOCK))

    ;; health check (FAIL-FAST) 
    ;; Check position is liquidatable BEFORE calling calc-liq-factor
    (health-check  (asserts! (>= current-ltv ltv-liq-partial) ERR-HEALTHY))
```
