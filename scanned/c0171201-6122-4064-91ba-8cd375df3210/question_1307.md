# Q1307: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With the moment the accrued MGP is drawn from the shared contract balance under attacker control and BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, can an unprivileged caller sequence `claimReward()` so that `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, have the attacker run `claimReward()`, then assert the victim's claimable value and the `userInfos[account].factor` versus `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` relation are unchanged by the attacker's transaction.
