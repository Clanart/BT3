# Q1699: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Starting from a state where totalSupply is zero because every voter has unvoted, can an unprivileged EOA use `updateFor(address _account) inherited from BaseRewardPoolV2` to leave `totalSupply` inconsistent with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violating the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish totalSupply is zero because every voter has unvoted, have the attacker run `updateFor(address _account) inherited from BaseRewardPoolV2`, then assert the victim's claimable value and the `totalSupply` versus `the sum of userVotedForPoolInVlmgp over all voters for this pool` relation are unchanged by the attacker's transaction.
