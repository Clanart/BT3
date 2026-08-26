# Q0453: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
In rewards/ReferralStorage.sol, cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Does `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` let an unprivileged caller exploit that under the attacker controls two addresses and binds one to the other's code, so that `userInfos[account].factor` diverges from `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, the invariant that totalBoostFactor must equal the sum of current per-user factors at all times is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the attacker controls two addresses and binds one to the other's code, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that totalBoostFactor must equal the sum of current per-user factors at all times.
