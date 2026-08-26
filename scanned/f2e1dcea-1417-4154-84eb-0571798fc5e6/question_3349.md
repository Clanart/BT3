# Q3349: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Does `updateFor(address _account) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, so that `_balances[account]` diverges from `totalSupply`, the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, snapshot `_balances[account]` and `totalSupply`, run the attacker's `updateFor(address _account) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
