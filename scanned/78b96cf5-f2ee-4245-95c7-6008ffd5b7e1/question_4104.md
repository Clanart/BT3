# Q4104: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Under a large vesting MGP distribution has just been queued into the vlMGP rewarder, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `userTotalVotedInVlmgp(user) in WombatBribeManager` unreconciled with `getUserTotalLocked(user)`, violates the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large vesting MGP distribution has just been queued into the vlMGP rewarder, have the attacker run `forceUnLock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp(user) in WombatBribeManager` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
