# Q1820: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
In rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an unprivileged attacker reach this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` while the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, and drive `userInfos[account].factor` out of agreement with `totalBoostFactor` - breaking the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim - for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
