# Q5883: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
In wombat/WombatBribeManager.sol, the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Does `unvote(address _lp)` let an unprivileged caller exploit that under the attacker has just cancelled a cooldown so getUserVotable jumped upward, so that `getVoteForLp(lp) from the Wombat voter` diverges from `poolInfos[lp].totalVoteInVlmgp`, the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `unvote(address _lp)`, and assert `getVoteForLp(lp) from the Wombat voter` equals `poolInfos[lp].totalVoteInVlmgp` and that no account can withdraw more than it put in.
