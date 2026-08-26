# Q3600: ReferralStorage.trigger - basic plus boosted is never bounded against DENOMINATOR

## Question
rewards/ReferralStorage.sol - setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under the attacker calls multiclaimFor on a set of referred accounts in one block, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `codeOwners[_code]` and `userInfos[account].myCode` and the invariant that the total percentage paid out on a claim must be bounded below one hundred percent of that claim, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: basic plus boosted is never bounded against DENOMINATOR)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: setTier() caps each tier's rewardPercentage at DENOMINATOR but trigger() uses basic + _calBoosted(referer) with no combined ceiling, so the pair of percentages applied to a claim can exceed one hundred percent of that claim. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: the total percentage paid out on a claim must be bounded below one hundred percent of that claim; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under the attacker calls multiclaimFor on a set of referred accounts in one block, asserting at the end that `codeOwners[_code]` still equals `userInfos[account].myCode` and the PoC's balance delta is non-positive.
