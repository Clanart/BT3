# Q2164: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol - _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker controlling the victim address and the block at which their bribe index is pinned, under the bribe token registered for this gauge charges a transfer fee, exploit this through `updateFor(address _account) inherited from BaseRewardPoolV2` to break the reconciliation between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` and the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account) inherited from BaseRewardPoolV2`: constrain the setup so that the bribe token registered for this gauge charges a transfer fee, fuzz the attacker inputs (the victim address and the block at which their bribe index is pinned), and assert after every call that a backlog accrued with no voters must not be assignable to a single one-block voter.
