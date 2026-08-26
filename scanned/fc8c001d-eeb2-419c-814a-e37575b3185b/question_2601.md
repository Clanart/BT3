# Q2601: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Under the attacker cancels a cooldown so their real lock rises with no factor refresh, is there an unprivileged sequence of `claimReward()` that leaves `tiers[tierId].rewardPercentage + _calBoosted(referer)` unreconciled with `DENOMINATOR`, violates the invariant that a boost weight must not reward splitting one position across addresses, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker cancels a cooldown so their real lock rises with no factor refresh, snapshot `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR`, run the attacker's `claimReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
