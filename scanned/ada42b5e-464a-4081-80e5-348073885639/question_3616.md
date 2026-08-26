# Q3616: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
In rewards/ReferralStorage.sol, _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker calls multiclaimFor on a set of referred accounts in one block, so that `myReferer[account]` diverges from `userInfos[account].codeIUsed`, the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls multiclaimFor on a set of referred accounts in one block, snapshot `myReferer[account]` and `userInfos[account].codeIUsed`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
