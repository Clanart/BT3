# Q2234: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
Note that in rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an attacker holding only tokens bought on market reach it via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` under the attacker locked vlMGP before registering a code and force `userInfos[account].factor` apart from `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, breaking the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under the attacker locked vlMGP before registering a code, asserting on every row that the total percentage paid out on a claim must be bounded below one hundred percent of that claim.
