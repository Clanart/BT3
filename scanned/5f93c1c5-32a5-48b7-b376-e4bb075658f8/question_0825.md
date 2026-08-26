# Q0825: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
In rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, so that `myReferer[account]` diverges from `userInfos[account].codeIUsed`, the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `myReferer[account]` equals `userInfos[account].codeIUsed` and that no account can withdraw more than it put in.
