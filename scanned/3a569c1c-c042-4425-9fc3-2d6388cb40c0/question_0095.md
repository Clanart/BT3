# Q0095: BaseRewardPool.updateFor - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol - the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the pool has exactly one registered reward token and no queued backlog, exploit this through `updateFor(address _account)` to break the reconciliation between `10**stakingDecimals()` and `totalStaked()` and the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the pool has exactly one registered reward token and no queued backlog, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual.
