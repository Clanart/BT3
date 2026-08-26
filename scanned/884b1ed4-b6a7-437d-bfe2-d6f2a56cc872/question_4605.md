# Q4605: VLMGP.cancelUnlock - forceUnLock never refreshes the referral boost factor

## Question
In VLMGP.sol, startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Starting from a state where the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, can an unprivileged EOA use `cancelUnlock(uint256 _slotIndex)` to leave `maxSlot` inconsistent with `userUnlockings[user].length`, violating the invariant that the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: forceUnLock never refreshes the referral boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: startUnlock() and _lock() both call IReferralStorage(referralStorage).updateTotalFactor(), but forceUnLock(), unlock() and cancelUnlock() do not, so userInfos[user].factor and the shared totalBoostFactor denominator drift away from the real getUserTotalLocked. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: the referral boost factor and its global denominator must be refreshed on every path that changes a user's locked balance; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, asserting at the end that `maxSlot` still equals `userUnlockings[user].length` and the PoC's balance delta is non-positive.
