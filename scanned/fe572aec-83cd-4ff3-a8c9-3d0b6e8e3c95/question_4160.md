# Q4160: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
rewards/ReferralStorage.sol - setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under sharePercent is set so most of the split goes to the referrer, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `BoostPoint` and `totalBoostFactor` and the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under sharePercent is set so most of the split goes to the referrer, asserting on every row that the total percentage paid out on a claim must be bounded below one hundred percent of that claim.
