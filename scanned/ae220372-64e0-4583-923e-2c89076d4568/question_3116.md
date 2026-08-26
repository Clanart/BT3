# Q3116: VLMGP.forceUnLock - expectedPenaltyAmount reads msg.sender rather than the slot owner

## Question
Note that in VLMGP.sol, expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Can an attacker holding only tokens bought on market reach it via `forceUnLock(uint256 _slotIndex)` under the pool the attacker voted for has since been deactivated so unvote reverts and force `userInfos[user].factor in ReferralStorage` apart from `getUserTotalLocked(user)`, breaking the invariant that a pricing helper used to settle a position must be parameterised by that position's owner for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: expectedPenaltyAmount reads msg.sender rather than the slot owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: a pricing helper used to settle a position must be parameterised by that position's owner; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool the attacker voted for has since been deactivated so unvote reverts, have the attacker run `forceUnLock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userInfos[user].factor in ReferralStorage` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
