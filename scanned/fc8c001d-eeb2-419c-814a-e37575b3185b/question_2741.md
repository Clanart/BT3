# Q2741: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
rewards/ReferralStorage.sol - updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under the attacker cancels a cooldown so their real lock rises with no factor refresh, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `codeOwners[_code]` and `userInfos[account].myCode` and the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker cancels a cooldown so their real lock rises with no factor refresh, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `codeOwners[_code]` versus `userInfos[account].myCode` relation are unchanged by the attacker's transaction.
