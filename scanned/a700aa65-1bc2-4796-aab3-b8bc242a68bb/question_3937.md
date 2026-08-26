# Q3937: BaseRewardPool.donateRewards - stakingDecimals sourced from an external metadata call

## Question
rewards/BaseRewardPool.sol: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: stakingDecimals sourced from an external metadata call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the reward scaling factor is 10**stakingDecimals(), and V1 re-reads IERC20Metadata(stakingToken).decimals() on every accrual, so any staking token whose reported decimals is not constant rescales every previously stored rewardPerTokenStored. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: the scaling factor used to store and to redeem rewardPerToken must be identical for the whole life of an accrual; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker funds the action with a flash loan of the staking token repaid in the same transaction, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
