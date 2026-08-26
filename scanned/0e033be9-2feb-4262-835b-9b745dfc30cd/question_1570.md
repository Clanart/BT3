# Q1570: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
wombat/WombatBribeManager.sol - the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Can an unprivileged attacker controlling _lp and the moment the whole position on that pool is released, under the attacker locks vlMGP, votes and casts inside a single transaction, exploit this through `unvote(address _lp)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `unvote(address _lp)` sequence atomically under the attacker locks vlMGP, votes and casts inside a single transaction, asserting at the end that `targetVote computed in castVotes` still equals `totalVotes() from veWom.balanceOf(wombatStaking)` and the PoC's balance delta is non-positive.
