# Q0407: VLMGP.cancelUnlock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, so that `userTotalVotedInVlmgp(user) in WombatBribeManager` diverges from `getUserTotalLocked(user)`, the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, then assert `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` end identical in both runs.
