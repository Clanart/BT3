# Q1362: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
Note that in rewards/ReferralStorage.sol, setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an attacker holding only tokens bought on market reach it via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR and force `BoostPoint` apart from `totalBoostFactor`, breaking the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, snapshot `BoostPoint` and `totalBoostFactor`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
