# Q0545: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. With the victim address and the block at which their bribe index is pinned under attacker control and a large bribe for the gauge is pending and no cast has run yet, can an unprivileged caller sequence `updateFor(address _account) inherited from BaseRewardPoolV2` so that `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` no longer reconcile, violating the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under a large bribe for the gauge is pending and no cast has run yet, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `totalSupply at the moment of the flush` and the PoC's balance delta is non-positive.
