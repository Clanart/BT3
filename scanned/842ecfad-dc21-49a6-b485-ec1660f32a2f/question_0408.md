# Q0408: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. With _slotIndex and the redemption timing under attacker control and the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `userUnlockings[user][i].amountInCoolDown` and `maxSlot` no longer reconcile, violating the invariant that a user must not lose vested value merely because they redeemed late and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, call `unlock(uint256 _slotIndex)`, and assert `userUnlockings[user][i].amountInCoolDown` equals `maxSlot` and that no account can withdraw more than it put in.
