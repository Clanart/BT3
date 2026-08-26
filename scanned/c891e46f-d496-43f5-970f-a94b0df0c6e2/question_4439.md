# Q4439: BaseRewardPool.updateFor - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol - the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the reward token charges a transfer fee so the received balance is below the requested amount, exploit this through `updateFor(address _account)` to break the reconciliation between `rewardTokens.length` and `isRewardToken[_rewardToken]` and the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the reward token charges a transfer fee so the received balance is below the requested amount, call `updateFor(address _account)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
