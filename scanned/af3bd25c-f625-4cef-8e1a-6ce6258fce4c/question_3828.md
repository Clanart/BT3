# Q3828: VLMGP.forceUnLock - totalPenalty accrues against a shared MGP balance

## Question
VLMGP.sol: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `getUserTotalLocked(user)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violates the invariant that penalty accounting must be backed by tokens that are not already owed to lockers as principal, and delivers Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: totalPenalty accrues against a shared MGP balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: forceUnLock() adds to totalPenalty while paying amountToUser out of the same IERC20(MGP) balance that backs every locker's principal, and transferPenalty() later sends the accumulated total away, so the penalty pot and the principal pot are the same tokens. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: penalty accounting must be backed by tokens that are not already owed to lockers as principal; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that penalty accounting must be backed by tokens that are not already owed to lockers as principal.
