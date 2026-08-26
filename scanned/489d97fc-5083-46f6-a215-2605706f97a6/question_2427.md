# Q2427: VLMGP.startUnlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `getUserTotalLocked(user)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violates the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the PoC's balance delta is non-positive.
