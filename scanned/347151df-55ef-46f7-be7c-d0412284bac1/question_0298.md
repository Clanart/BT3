# Q0298: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
rewards/ReferralStorage.sol: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Under the attacker controls two addresses and binds one to the other's code, is there an unprivileged sequence of `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` that leaves `myReferer[account]` unreconciled with `userInfos[account].codeIUsed`, violates the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under the attacker controls two addresses and binds one to the other's code, asserting on every row that a shared boost budget must be diluted by absolute participation, not only by relative share.
