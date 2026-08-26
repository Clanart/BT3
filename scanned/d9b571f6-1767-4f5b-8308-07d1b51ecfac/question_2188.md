# Q2188: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
Consider rewards/ReferralStorage.sol, where userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Assuming the attacker locked vlMGP before registering a code, can an unprivileged attacker turn this into a divergence between `refererPercentage + refereePercentage` and `DENOMINATOR` via `claimReward()`, breaking the invariant that a boost weight must not reward splitting one position across addresses and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the moment the accrued MGP is drawn from the shared contract balance) under the attacker locked vlMGP before registering a code, asserting on every row that a boost weight must not reward splitting one position across addresses.
