# Q4653: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol - startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling _slotIndex and the exact point inside the cooldown curve at which the penalty is priced, under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, exploit this through `forceUnLock(uint256 _slotIndex)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` end identical in both runs.
