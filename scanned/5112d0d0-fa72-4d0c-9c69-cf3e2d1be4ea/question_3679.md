# Q3679: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Under the attacker calls the inherited donateRewards for the registered bribe token, is there an unprivileged sequence of `updateFor(address _account) inherited from BaseRewardPoolV2` that leaves `totalSupply` unreconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violates the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account) inherited from BaseRewardPoolV2`: constrain the setup so that the attacker calls the inherited donateRewards for the registered bribe token, fuzz the attacker inputs (the victim address and the block at which their bribe index is pinned), and assert after every call that a backlog accrued with no voters must not be assignable to a single one-block voter.
