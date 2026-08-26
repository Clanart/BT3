# Q2727: VLMGP.forceUnLock - expectedPenaltyAmount reads msg.sender rather than the slot owner

## Question
VLMGP.sol: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. With _slotIndex and the exact point inside the cooldown curve at which the penalty is priced under attacker control and the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged caller sequence `forceUnLock(uint256 _slotIndex)` so that `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` no longer reconcile, violating the invariant that a pricing helper used to settle a position must be parameterised by that position's owner and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: expectedPenaltyAmount reads msg.sender rather than the slot owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: a pricing helper used to settle a position must be parameterised by that position's owner; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, snapshot `totalPenalty` and `IERC20(MGP).balanceOf(address(this))`, run the attacker's `forceUnLock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
