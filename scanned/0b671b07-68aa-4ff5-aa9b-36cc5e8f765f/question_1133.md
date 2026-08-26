# Q1133: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
rewards/BribeRewardPool.sol - updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Can an unprivileged attacker controlling the victim address and the block at which their bribe index is pinned, under the attacker votes and casts inside one transaction through voteAndCast, exploit this through `updateFor(address _account) inherited from BaseRewardPoolV2` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that only the account or the operator on a real vote change may advance a voter's index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under the attacker votes and casts inside one transaction through voteAndCast, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `totalSupply at the moment of the flush` and the PoC's balance delta is non-positive.
