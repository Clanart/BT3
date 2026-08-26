# Q1087: BaseRewardPool.donateRewards - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPool.sol - totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that sum over all accounts of balanceOf(account) must equal totalStaked(), yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, snapshot `rewardTokens.length` and `isRewardToken[_rewardToken]`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
