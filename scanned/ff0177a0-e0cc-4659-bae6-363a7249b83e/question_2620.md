# Q2620: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol - _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker controlling the victim address and the block at which their bribe index is pinned, under the bribe token has begun reverting on transfer, exploit this through `updateFor(address _account) inherited from BaseRewardPoolV2` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under the bribe token has begun reverting on transfer, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.
