# Q2241: BaseRewardPool.donateRewards - totalStaked and balanceOf read from different sources

## Question
Note that in rewards/BaseRewardPool.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18 and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
