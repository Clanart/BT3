# Q0624: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
In VLMGP.sol, forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Starting from a state where the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `totalAmount` inconsistent with `sum of userInfo[vlmgp][*].amount in MasterMagpie`, violating the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal and extracting Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, call `forceUnLock(uint256 _slotIndex)`, and assert `totalAmount` equals `sum of userInfo[vlmgp][*].amount in MasterMagpie` and that no account can withdraw more than it put in.
