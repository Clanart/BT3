# Q3333: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
rewards/ReferralStorage.sol: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. With the referee address and the block, because multiclaimFor is permissionless under attacker control and the referee has a large pending MGP claim in MasterMagpie, can an unprivileged caller sequence `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` so that `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` no longer reconcile, violating the invariant that every accrued entitlement must be backed by tokens already held and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the referee has a large pending MGP claim in MasterMagpie, then assert `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` end identical in both runs.
