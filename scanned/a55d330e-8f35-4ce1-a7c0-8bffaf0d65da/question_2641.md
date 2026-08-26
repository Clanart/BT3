# Q2641: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
In rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker cancels a cooldown so their real lock rises with no factor refresh, so that `userInfos[account].rewardAmount` diverges from `MGP.balanceOf(address(this))`, the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker cancels a cooldown so their real lock rises with no factor refresh, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `userInfos[account].rewardAmount` equals `MGP.balanceOf(address(this))` and that no account can withdraw more than it put in.
