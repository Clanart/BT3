# Q3316: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
rewards/ReferralStorage.sol: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. With the referee address and the block, because multiclaimFor is permissionless under attacker control and the referee has a large pending MGP claim in MasterMagpie, can an unprivileged caller sequence `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` so that `codeOwners[_code]` and `userInfos[account].myCode` no longer reconcile, violating the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the referee has a large pending MGP claim in MasterMagpie, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
