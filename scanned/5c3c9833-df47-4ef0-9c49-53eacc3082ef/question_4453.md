# Q4453: BaseRewardPoolV2.updateFor - totalStaked and balanceOf read from different sources

## Question
rewards/BaseRewardPoolV2.sol: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the attacker calls the function twice in the same block to observe the second, early-continued iteration, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the attacker calls the function twice in the same block to observe the second, early-continued iteration, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that sum over all accounts of balanceOf(account) must equal totalStaked().
