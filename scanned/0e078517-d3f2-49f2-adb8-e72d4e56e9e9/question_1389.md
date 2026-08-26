# Q1389: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
Consider rewards/ReferralStorage.sol, where _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Assuming BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, can an unprivileged attacker turn this into a divergence between `userInfos[account].factor` and `totalBoostFactor` via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, breaking the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, snapshot `userInfos[account].factor` and `totalBoostFactor`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
