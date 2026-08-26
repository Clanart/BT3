# Q3979: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
Consider rewards/BribeRewardPool.sol, where updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Assuming the victim has a large unsettled bribe balance, can an unprivileged attacker turn this into a divergence between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that only the account or the operator on a real vote change may advance a voter's index and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled bribe balance, call `updateFor(address _account) inherited from BaseRewardPoolV2`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
