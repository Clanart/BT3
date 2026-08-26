# Q1236: BaseRewardPoolV2.updateFor - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPoolV2.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Can an unprivileged attacker reach this through `updateFor(address _account)` while V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, and drive `rewardTokens.length` out of agreement with `isRewardToken[_rewardToken]` - breaking the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under V2 caches stakingTokenDecimals at construction and both _updateFor and the updateRewards modifier early-continue when userRewardPerTokenPaid equals rewardPerTokenStored, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.
