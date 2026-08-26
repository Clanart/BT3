# Q0965: VLMGP.unlock - forceUnLock never refreshes the referral boost factor

## Question
Consider VLMGP.sol, where startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Assuming the attacker's slot matured exactly one second ago, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` via `unlock(uint256 _slotIndex)`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that the attacker's slot matured exactly one second ago, fuzz the attacker inputs (_slotIndex and how long after endTime the slot is redeemed), and assert after every call that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance.
