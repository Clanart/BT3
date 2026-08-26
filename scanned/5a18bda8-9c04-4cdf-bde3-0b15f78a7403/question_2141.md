# Q2141: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
rewards/BribeRewardPool.sol - updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Can an unprivileged attacker controlling the victim address and the block at which their bribe index is pinned, under the bribe token registered for this gauge charges a transfer fee, exploit this through `updateFor(address _account) inherited from BaseRewardPoolV2` to break the reconciliation between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the invariant that only the account or the operator on a real vote change may advance a voter's index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their bribe index is pinned) under the bribe token registered for this gauge charges a transfer fee, asserting on every row that only the account or the operator on a real vote change may advance a voter's index.
