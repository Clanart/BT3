# Q3452: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol - userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under the referee has a large pending MGP claim in MasterMagpie, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that a boost weight must not reward splitting one position across addresses, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the target account, because lockFor is permissionless) under the referee has a large pending MGP claim in MasterMagpie, asserting on every row that a boost weight must not reward splitting one position across addresses.
