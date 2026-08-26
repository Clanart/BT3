# Q0422: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
In rewards/ReferralStorage.sol, updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Does `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` let an unprivileged caller exploit that under the attacker controls two addresses and binds one to the other's code, so that `userInfos[account].factor` diverges from `totalBoostFactor`, the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker controls two addresses and binds one to the other's code, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `userInfos[account].factor` versus `totalBoostFactor` relation are unchanged by the attacker's transaction.
