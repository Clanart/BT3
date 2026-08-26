# Q2578: BribeRewardPool.donateRewards - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol - BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an unprivileged attacker controlling _amountReward and which already-registered bribe token is provisioned, under the bribe token has begun reverting on transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to break the reconciliation between `_balances[account]` and `totalSupply` and the invariant that all reward math in a contract must read the balance ledger the contract actually maintains, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token has begun reverting on transfer, snapshot `_balances[account]` and `totalSupply`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
