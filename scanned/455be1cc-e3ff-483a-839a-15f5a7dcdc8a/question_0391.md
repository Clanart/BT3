# Q0391: ReferralStorage.trigger - sqrt factor makes many small accounts dominate the denominator

## Question
Note that in rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an attacker holding only tokens bought on market reach it via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` under the attacker controls two addresses and binds one to the other's code and force `userInfos[account].factor` apart from `totalBoostFactor`, breaking the invariant that a boost weight must not reward splitting one position across addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under the attacker controls two addresses and binds one to the other's code, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
