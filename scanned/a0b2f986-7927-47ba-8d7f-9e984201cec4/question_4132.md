# Q4132: BaseRewardPool.updateFor - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the victim has not been settled for several epochs and holds a large userRewards balance, can an unprivileged caller sequence `updateFor(address _account)` so that `10**stakingDecimals()` and `totalStaked()` no longer reconcile, violating the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has not been settled for several epochs and holds a large userRewards balance, call `updateFor(address _account)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
