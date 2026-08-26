# Q3133: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
In VLMGP.sol, forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Can an unprivileged attacker reach this through `forceUnLock(uint256 _slotIndex)` while the pool the attacker voted for has since been deactivated so unvote reverts, and drive `userTotalVotedInVlmgp(user) in WombatBribeManager` out of agreement with `getUserTotalLocked(user)` - breaking the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `forceUnLock(uint256 _slotIndex)`: constrain the setup so that the pool the attacker voted for has since been deactivated so unvote reverts, fuzz the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced), and assert after every call that penalty accounting must be backed by tokens that are not already owed to lockers as principal.
