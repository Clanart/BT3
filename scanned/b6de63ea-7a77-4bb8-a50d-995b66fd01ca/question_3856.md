# Q3856: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
Note that in rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an attacker holding only tokens bought on market reach it via `claimReward()` under sharePercent is set so most of the split goes to the referee and force `userInfos[account].factor` apart from `totalBoostFactor`, breaking the invariant that a boost weight must not reward splitting one position across addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the moment the accrued MGP is drawn from the shared contract balance) under sharePercent is set so most of the split goes to the referee, asserting on every row that a boost weight must not reward splitting one position across addresses.
