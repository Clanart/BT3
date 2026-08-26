# Q4149: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
In VLMGP.sol, forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Can an unprivileged attacker reach this through `forceUnLock(uint256 _slotIndex)` while a large vesting MGP distribution has just been queued into the vlMGP rewarder, and drive `getUserAmountInCoolDown(user)` out of agreement with `totalAmountInCoolDown` - breaking the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal - for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under a large vesting MGP distribution has just been queued into the vlMGP rewarder, asserting on every row that penalty accounting must be backed by tokens that are not already owed to lockers as principal.
