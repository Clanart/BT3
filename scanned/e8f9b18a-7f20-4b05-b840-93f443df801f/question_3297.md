# Q3297: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
In wombat/WombatBribeManager.sol, the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `unvote(address _lp)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `unvote(address _lp)` sequence atomically under the pool the attacker voted for has been deactivated so unvote reverts, asserting at the end that `poolInfos[lp].isActive` still equals `userVotedForPoolInVlmgp[user][lp]` and the PoC's balance delta is non-positive.
