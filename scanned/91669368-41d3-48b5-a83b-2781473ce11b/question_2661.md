# Q2661: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
Consider rewards/ReferralStorage.sol, where _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Assuming the attacker cancels a cooldown so their real lock rises with no factor refresh, can an unprivileged attacker turn this into a divergence between `refererPercentage + refereePercentage` and `DENOMINATOR` via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, breaking the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker cancels a cooldown so their real lock rises with no factor refresh, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `refererPercentage + refereePercentage` equals `DENOMINATOR` and that no account can withdraw more than it put in.
