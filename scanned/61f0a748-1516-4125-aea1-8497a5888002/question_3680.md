# Q3680: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
rewards/ReferralStorage.sol: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Under the attacker calls multiclaimFor on a set of referred accounts in one block, is there an unprivileged sequence of `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` that leaves `userInfos[account].factor` unreconciled with `totalBoostFactor`, violates the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the attacker calls multiclaimFor on a set of referred accounts in one block, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that the boost factor and its denominator must be refreshed on every path that changes a locked balance.
