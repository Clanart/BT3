# Q4556: BaseRewardPool.donateRewards - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPool.sol: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the reward token charges a transfer fee so the received balance is below the requested amount, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `rewardTokens.length` and `isRewardToken[_rewardToken]` no longer reconcile, violating the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the reward token charges a transfer fee so the received balance is below the requested amount, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `rewardTokens.length` versus `isRewardToken[_rewardToken]` relation are unchanged by the attacker's transaction.
