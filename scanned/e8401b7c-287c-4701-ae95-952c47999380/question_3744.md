# Q3744: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker reach this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` while the attacker calls multiclaimFor on a set of referred accounts in one block, and drive `userInfos[account].factor` out of agreement with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` - breaking the invariant that a boost weight must not reward splitting one position across addresses - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls multiclaimFor on a set of referred accounts in one block, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `userInfos[account].factor` versus `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` relation are unchanged by the attacker's transaction.
