# Q3009: BaseRewardPool.updateFor - stakingDecimals sourced from an external metadata call

## Question
Note that in rewards/BaseRewardPool.sol, the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual for Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual.
