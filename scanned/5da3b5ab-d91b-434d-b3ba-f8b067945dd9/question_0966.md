# Q0966: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Under the attacker's slot matured one block ago, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `mWomSV.getUserTotalLocked(user)` unreconciled with `ArbWomUp3.calDoubledCounted(user)`, violates the invariant that a user must not lose vested value merely because they redeemed late, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's slot matured one block ago, snapshot `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)`, run the attacker's `unlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
