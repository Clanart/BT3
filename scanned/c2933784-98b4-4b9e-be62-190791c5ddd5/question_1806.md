# Q1806: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
VLMGP.sol: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. With _slotIndex and the exact point inside the cooldown curve at which the penalty is priced under attacker control and coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged caller sequence `forceUnLock(uint256 _slotIndex)` so that `userUnlockings[user][i].endTime` and `block.timestamp` no longer reconcile, violating the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal and realising Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `forceUnLock(uint256 _slotIndex)`: constrain the setup so that coolDownInSecs is at its configured production value and endTime is far in the future, fuzz the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced), and assert after every call that penalty accounting must be backed by tokens that are not already owed to lockers as principal.
