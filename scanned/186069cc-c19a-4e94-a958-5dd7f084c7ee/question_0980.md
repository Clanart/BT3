# Q0980: ReferralStorage.updateTotalFactor - totalBoostFactor is only refreshed on two of the paths that change a lock

## Question
In rewards/ReferralStorage.sol, updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Starting from a state where the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged EOA use `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to leave `userInfos[account].factor` inconsistent with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, violating the invariant that the boost factor and its denominator must be refreshed on every path that changes a locked balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: totalBoostFactor is only refreshed on two of the paths that change a lock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() is called from VLMGP._lock and VLMGP.startUnlock but not from unlock, cancelUnlock or forceUnLock, so the stored factor and the shared denominator drift away from the real getUserTotalLocked. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: the boost factor and its denominator must be refreshed on every path that changes a locked balance; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, asserting at the end that `userInfos[account].factor` still equals `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the PoC's balance delta is non-positive.
