# Q2991: BaseRewardPool.updateFor - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPool.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `totalStaked()` inconsistent with `IERC20(stakingToken).balanceOf(operator)`, violating the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting at the end that `totalStaked()` still equals `IERC20(stakingToken).balanceOf(operator)` and the PoC's balance delta is non-positive.
