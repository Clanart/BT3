# Q3950: ReferralStorage.trigger - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under sharePercent is set so most of the split goes to the referee, so that `userInfos[account].factor` diverges from `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, the invariant that a boost weight must not reward splitting one position across addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that sharePercent is set so most of the split goes to the referee, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that a boost weight must not reward splitting one position across addresses.
