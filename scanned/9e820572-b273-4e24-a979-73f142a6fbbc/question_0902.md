# Q0902: BaseRewardPoolV2.donateRewards - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPoolV2.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Starting from a state where rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewardTokens.length` inconsistent with `isRewardToken[_rewardToken]`, violating the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting at the end that `rewardTokens.length` still equals `isRewardToken[_rewardToken]` and the PoC's balance delta is non-positive.
