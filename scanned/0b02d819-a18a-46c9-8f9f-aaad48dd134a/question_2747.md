# Q2747: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
Note that in VLMGP.sol, forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Can an attacker holding only tokens bought on market reach it via `forceUnLock(uint256 _slotIndex)` under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked and force `userInfos[user].factor in ReferralStorage` apart from `getUserTotalLocked(user)`, breaking the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal for Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, snapshot `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)`, run the attacker's `forceUnLock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
