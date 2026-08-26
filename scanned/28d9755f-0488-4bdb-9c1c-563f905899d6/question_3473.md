# Q3473: VLMGP.forceUnLock - expectedPenaltyAmount reads msg.sender rather than the slot owner

## Question
In VLMGP.sol, expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Starting from a state where the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `userTotalVotedInVlmgp(user) in WombatBribeManager` inconsistent with `getUserTotalLocked(user)`, violating the invariant that a pricing helper used to settle a position must be parameterised by that position's owner and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: expectedPenaltyAmount reads msg.sender rather than the slot owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: a pricing helper used to settle a position must be parameterised by that position's owner; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting on every row that a pricing helper used to settle a position must be parameterised by that position's owner.
