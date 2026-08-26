# Q0949: ReferralStorage.trigger - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol - userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the invariant that a boost weight must not reward splitting one position across addresses, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, then assert `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` end identical in both runs.
