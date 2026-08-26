# Q3404: BaseRewardPoolV2.donateRewards - queuedRewards flush timed by the attacker

## Question
rewards/BaseRewardPoolV2.sol: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violates the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
