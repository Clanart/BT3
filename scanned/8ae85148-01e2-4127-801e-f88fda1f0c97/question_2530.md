# Q2530: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
wombat/WombatBribeManager.sol - the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Can an unprivileged attacker controlling _lp and the moment the whole position on that pool is released, under the attacker votes in the block immediately before a known keeper cast, exploit this through `unvote(address _lp)` to break the reconciliation between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` and the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lp and the moment the whole position on that pool is released) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active.
