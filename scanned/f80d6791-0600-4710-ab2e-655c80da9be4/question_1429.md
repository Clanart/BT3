# Q1429: VLMGP.startUnlock - forceUnLock never refreshes the referral boost factor

## Question
VLMGP.sol: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. With _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot under attacker control and coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish coolDownInSecs is at its configured production value and endTime is far in the future, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp(user) in WombatBribeManager` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
