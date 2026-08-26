# Q1935: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
Note that in rewards/ReferralStorage.sol, updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values and force `refererPercentage + refereePercentage` apart from `DENOMINATOR`, breaking the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`: constrain the setup so that the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, fuzz the attacker inputs (the target account, because lockFor is permissionless), and assert after every call that the boost factor and its denominator must be refreshed on every path that changes a locked balance.
