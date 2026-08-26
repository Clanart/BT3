# Q1206: BaseRewardPool.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
Consider rewards/BaseRewardPool.sol, where _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Assuming rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged attacker turn this into a divergence between `10**stakingDecimals()` and `totalStaked()` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that the amount credited to the index must equal the balance delta actually received by the pool and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
