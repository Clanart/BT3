# Q3922: BaseRewardPool.donateRewards - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPool.sol: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `rewards[_rewardToken].historicalRewards` unreconciled with `IERC20(_rewardToken).balanceOf(address(this))`, violates the invariant that sum over all accounts of balanceOf(account) must equal totalStaked(), and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, asserting on every row that sum over all accounts of balanceOf(account) must equal totalStaked().
