# Q1649: BribeRewardPool.donateRewards - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Starting from a state where totalSupply is zero because every voter has unvoted, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up totalSupply is zero because every voter has unvoted, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
