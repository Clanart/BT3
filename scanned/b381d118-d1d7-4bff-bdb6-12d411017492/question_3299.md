# Q3299: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
rewards/ReferralStorage.sol: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Under the referee has a large pending MGP claim in MasterMagpie, is there an unprivileged sequence of `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` that leaves `tiers[tierId].rewardPercentage + _calBoosted(referer)` unreconciled with `DENOMINATOR`, violates the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the referee has a large pending MGP claim in MasterMagpie, then assert `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` end identical in both runs.
