# Q3401: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
rewards/ReferralStorage.sol: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. With the target account, because lockFor is permissionless under attacker control and the referee has a large pending MGP claim in MasterMagpie, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `userInfos[account].factor` and `totalBoostFactor` no longer reconcile, violating the invariant that totalBoostFactor must equal the sum of current per-user factors at all times and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the referee has a large pending MGP claim in MasterMagpie, then assert `userInfos[account].factor` and `totalBoostFactor` end identical in both runs.
