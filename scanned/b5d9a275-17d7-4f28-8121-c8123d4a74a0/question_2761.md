# Q2761: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
rewards/ReferralStorage.sol: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. With the target account, because lockFor is permissionless under attacker control and the attacker cancels a cooldown so their real lock rises with no factor refresh, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `myReferer[account]` and `userInfos[account].codeIUsed` no longer reconcile, violating the invariant that totalBoostFactor must equal the sum of current per-user factors at all times and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker cancels a cooldown so their real lock rises with no factor refresh, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `myReferer[account]` versus `userInfos[account].codeIUsed` relation are unchanged by the attacker's transaction.
