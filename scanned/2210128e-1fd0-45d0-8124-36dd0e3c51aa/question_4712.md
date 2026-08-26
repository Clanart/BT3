# Q4712: BaseRewardPool.updateFor - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPool.sol - totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under a previously registered reward token has begun reverting on transfer, exploit this through `updateFor(address _account)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that sum over all accounts of balanceOf(account) must equal totalStaked(), yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a previously registered reward token has begun reverting on transfer, call `updateFor(address _account)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
