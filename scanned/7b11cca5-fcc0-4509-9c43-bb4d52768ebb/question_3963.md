# Q3963: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
In wombat/WombatBribeManager.sol, the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Does `unvote(address _lp)` let an unprivileged caller exploit that under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lp and the moment the whole position on that pool is released) under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, asserting on every row that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active.
