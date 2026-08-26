# Q1265: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
VLMGP.sol - forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Can an unprivileged attacker controlling _slotIndex and the exact point inside the cooldown curve at which the penalty is priced, under the attacker's slot matured exactly one second ago, exploit this through `forceUnLock(uint256 _slotIndex)` to break the reconciliation between `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` and the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker's slot matured exactly one second ago, asserting on every row that penalty accounting must be backed by tokens that are not already owed to lockers as principal.
