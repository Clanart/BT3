# Q0205: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the attacker controls two addresses and binds one to the other's code, can an unprivileged EOA use `claimReward()` to leave `BoostPoint` inconsistent with `totalBoostFactor`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker controls two addresses and binds one to the other's code, snapshot `BoostPoint` and `totalBoostFactor`, run the attacker's `claimReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
