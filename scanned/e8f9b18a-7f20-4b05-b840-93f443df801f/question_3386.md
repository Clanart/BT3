# Q3386: BaseRewardPool.updateFor - totalStaked and balanceOf read from different sources

## Question
Note that in rewards/BaseRewardPool.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under a reward-manager queueNewRewards transaction is pending in the mempool and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a reward-manager queueNewRewards transaction is pending in the mempool, call `updateFor(address _account)`, and assert `balanceOf(account)` equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
