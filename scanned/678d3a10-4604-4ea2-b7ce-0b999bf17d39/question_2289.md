# Q2289: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
VLMGP.sol - forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Can an unprivileged attacker controlling _slotIndex and the exact point inside the cooldown curve at which the penalty is priced, under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, exploit this through `forceUnLock(uint256 _slotIndex)` to break the reconciliation between `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` and the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal, yielding Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, then assert `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` end identical in both runs.
