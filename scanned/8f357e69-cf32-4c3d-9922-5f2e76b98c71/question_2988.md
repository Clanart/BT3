# Q2988: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
In rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an unprivileged attacker reach this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` while the attacker splits one large lock across many addresses that each register a code, and drive `refererPercentage + refereePercentage` out of agreement with `DENOMINATOR` - breaking the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim - for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under the attacker splits one large lock across many addresses that each register a code, asserting on every row that the total percentage paid out on a claim must be bounded below one hundred percent of that claim.
