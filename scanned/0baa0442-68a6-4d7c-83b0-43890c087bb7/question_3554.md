# Q3554: BaseRewardPool.donateRewards - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPool.sol: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Under a reward-manager queueNewRewards transaction is pending in the mempool, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that sum over all accounts of balanceOf(account) must equal totalStaked(), and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a reward-manager queueNewRewards transaction is pending in the mempool, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewards[_rewardToken].queuedRewards` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
