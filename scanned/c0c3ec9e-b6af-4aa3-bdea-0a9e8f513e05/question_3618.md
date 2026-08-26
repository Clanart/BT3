# Q3618: BaseRewardPool.donateRewards - fee-on-transfer or rebasing reward token inflates the index

## Question
Note that in rewards/BaseRewardPool.sol, _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under a reward-manager queueNewRewards transaction is pending in the mempool and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that the amount credited to the index must equal the balance delta actually received by the pool for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: fee-on-transfer or rebasing reward token inflates the index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() credits the full _amountReward to historicalRewards and to the rewardPerTokenStored increment based on the requested amount rather than the balance actually received, so any reward token that delivers less than requested promises more than the pool holds. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: the amount credited to the index must equal the balance delta actually received by the pool; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that the amount credited to the index must equal the balance delta actually received by the pool.
