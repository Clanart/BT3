# Q0531: VLMGP.forceUnLock - forceUnLock never refreshes the referral boost factor

## Question
Consider VLMGP.sol, where startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Assuming the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, can an unprivileged attacker turn this into a divergence between `maxSlot` and `userUnlockings[user].length` via `forceUnLock(uint256 _slotIndex)`, breaking the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, call `forceUnLock(uint256 _slotIndex)`, and assert `maxSlot` equals `userUnlockings[user].length` and that no account can withdraw more than it put in.
