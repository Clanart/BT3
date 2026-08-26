# Q2349: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
rewards/ReferralStorage.sol - updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under the attacker locked vlMGP before registering a code, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` and the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker locked vlMGP before registering a code, asserting at the end that `tiers[tierId].rewardPercentage + _calBoosted(referer)` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
