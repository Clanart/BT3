# Q3372: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the mWOM balance of the locker is exactly equal to totalAmount before the action, so that `mWomSV.getUserTotalLocked(user)` diverges from `ArbWomUp3.calDoubledCounted(user)`, the invariant that a user must not lose vested value merely because they redeemed late is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the mWOM balance of the locker is exactly equal to totalAmount before the action, asserting at the end that `mWomSV.getUserTotalLocked(user)` still equals `ArbWomUp3.calDoubledCounted(user)` and the PoC's balance delta is non-positive.
