# Q0808: BaseRewardPool.updateFor - stakingDecimals sourced from an external metadata call

## Question
In rewards/BaseRewardPool.sol, the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Does `updateFor(address _account)` let an unprivileged caller exploit that under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, so that `rewardTokens.length` diverges from `isRewardToken[_rewardToken]`, the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual.
