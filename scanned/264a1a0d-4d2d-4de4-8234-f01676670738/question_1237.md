# Q1237: VLMGP.forceUnLock - expectedPenaltyAmount reads msg.sender rather than the slot owner

## Question
VLMGP.sol: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Under the attacker's slot matured exactly one second ago, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `totalAmount` unreconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`, violates the invariant that a pricing helper used to settle a position must be parameterised by that position's owner, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: expectedPenaltyAmount reads msg.sender rather than the slot owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: a pricing helper used to settle a position must be parameterised by that position's owner; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's slot matured exactly one second ago, have the attacker run `forceUnLock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalAmount` versus `sum of userInfo[vlmgp][*].amount in MasterMagpie` relation are unchanged by the attacker's transaction.
