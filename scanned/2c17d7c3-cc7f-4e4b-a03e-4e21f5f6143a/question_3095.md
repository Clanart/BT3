# Q3095: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
rewards/ReferralStorage.sol: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Under the attacker splits one large lock across many addresses that each register a code, is there an unprivileged sequence of `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` that leaves `BoostPoint` unreconciled with `totalBoostFactor`, violates the invariant that totalBoostFactor must equal the sum of current per-user factors at all times, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker splits one large lock across many addresses that each register a code, asserting at the end that `BoostPoint` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
