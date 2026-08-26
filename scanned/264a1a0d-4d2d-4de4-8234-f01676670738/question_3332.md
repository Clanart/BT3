# Q3332: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
In rewards/BribeRewardPool.sol, updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Starting from a state where the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, can an unprivileged EOA use `updateFor(address _account) inherited from BaseRewardPoolV2` to leave `rewards[_rewardToken].queuedRewards` inconsistent with `totalSupply at the moment of the flush`, violating the invariant that only the account or the operator on a real vote change may advance a voter's index and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, then assert `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` end identical in both runs.
