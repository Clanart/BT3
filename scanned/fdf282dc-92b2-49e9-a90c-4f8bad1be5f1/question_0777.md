# Q0777: BaseRewardPool.updateFor - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPool.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Does `updateFor(address _account)` let an unprivileged caller exploit that under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, so that `10**stakingDecimals()` diverges from `totalStaked()`, the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `10**stakingDecimals()` versus `totalStaked()` relation are unchanged by the attacker's transaction.
