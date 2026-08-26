# Q0514: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
rewards/BribeRewardPool.sol: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. With the victim address and the block at which their bribe index is pinned under attacker control and a large bribe for the gauge is pending and no cast has run yet, can an unprivileged caller sequence `updateFor(address _account) inherited from BaseRewardPoolV2` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that only the account or the operator on a real vote change may advance a voter's index and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a large bribe for the gauge is pending and no cast has run yet, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `updateFor(address _account) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
