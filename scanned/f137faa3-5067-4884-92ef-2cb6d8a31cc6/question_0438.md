# Q0438: VLMGP.cancelUnlock - cancelUnlock raises the locked balance without refreshing the boost factor

## Question
In VLMGP.sol, cancelUnlock() zeroes slot.amountInCoolDown and reduces totalAmountInCoolDown, which raises getUserTotalLocked immediately, yet it never calls updateTotalFactor, so the shared totalBoostFactor understates real locked weight and inflates every other referrer's _calBoosted share. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, so that `maxSlot` diverges from `userUnlockings[user].length`, the invariant that totalBoostFactor must equal the sum of the current per-user factors at all times is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock raises the locked balance without refreshing the boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: cancelUnlock() zeroes slot.amountInCoolDown and reduces totalAmountInCoolDown, which raises getUserTotalLocked immediately, yet it never calls updateTotalFactor, so the shared totalBoostFactor understates real locked weight and inflates every other referrer's _calBoosted share. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: totalBoostFactor must equal the sum of the current per-user factors at all times; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, snapshot `maxSlot` and `userUnlockings[user].length`, run the attacker's `cancelUnlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
