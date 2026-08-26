# Q4175: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
rewards/ReferralStorage.sol - _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under sharePercent is set so most of the split goes to the referrer, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange sharePercent is set so most of the split goes to the referrer, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `userInfos[account].factor` equals `totalBoostFactor` and that no account can withdraw more than it put in.
