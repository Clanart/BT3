# Q0080: BribeRewardPool.stakeFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Does `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` let an unprivileged caller exploit that under a large bribe for the gauge is pending and no cast has run yet, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe for the gauge is pending and no cast has run yet, call `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
