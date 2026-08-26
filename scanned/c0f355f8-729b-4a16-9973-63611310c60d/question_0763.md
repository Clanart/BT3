# Q0763: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With the moment the accrued MGP is drawn from the shared contract balance under attacker control and the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged caller sequence `claimReward()` so that `userInfos[account].factor` and `totalBoostFactor` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the moment the accrued MGP is drawn from the shared contract balance) under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, asserting on every row that a boost weight must not reward splitting one position across addresses.
