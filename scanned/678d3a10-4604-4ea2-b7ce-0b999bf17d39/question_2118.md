# Q2118: BribeRewardPool.donateRewards - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` while the bribe token registered for this gauge charges a transfer fee, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `totalSupply at the moment of the flush` - breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe token registered for this gauge charges a transfer fee, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `rewards[_rewardToken].queuedRewards` equals `totalSupply at the moment of the flush` and that no account can withdraw more than it put in.
