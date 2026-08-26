# Q3732: VLMGP.cancelUnlock - cancelUnlock raises the locked balance without refreshing the boost factor

## Question
In VLMGP.sol, cancelUnlock() zeroes slot.amountInCoolDown and reduces totalAmountInCoolDown, which raises getUserTotalLocked immediately, yet it never calls updateTotalFactor, so the shared totalBoostFactor understates real locked weight and inflates every other referrer's _calBoosted share. Starting from a state where the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged EOA use `cancelUnlock(uint256 _slotIndex)` to leave `userInfos[user].factor in ReferralStorage` inconsistent with `getUserTotalLocked(user)`, violating the invariant that totalBoostFactor must equal the sum of the current per-user factors at all times and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock raises the locked balance without refreshing the boost factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: cancelUnlock() zeroes slot.amountInCoolDown and reduces totalAmountInCoolDown, which raises getUserTotalLocked immediately, yet it never calls updateTotalFactor, so the shared totalBoostFactor understates real locked weight and inflates every other referrer's _calBoosted share. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: totalBoostFactor must equal the sum of the current per-user factors at all times; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting at the end that `userInfos[user].factor in ReferralStorage` still equals `getUserTotalLocked(user)` and the PoC's balance delta is non-positive.
