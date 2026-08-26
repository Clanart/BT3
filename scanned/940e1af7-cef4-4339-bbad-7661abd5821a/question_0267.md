# Q0267: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
rewards/ReferralStorage.sol: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Under the attacker controls two addresses and binds one to the other's code, is there an unprivileged sequence of `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` that leaves `codeOwners[_code]` unreconciled with `userInfos[account].myCode`, violates the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that the attacker controls two addresses and binds one to the other's code, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that the total percentage paid out on a claim must be bounded below one hundred percent of that claim.
