# Q5801: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
In wombat/WombatBribeManager.sol, the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Starting from a state where the victim has a large unsettled balance in the pool rewarder, can an unprivileged EOA use `unvote(address _lp)` to leave `targetVote computed in castVotes` inconsistent with `totalVotes() from veWom.balanceOf(wombatStaking)`, violating the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `unvote(address _lp)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (_lp and the moment the whole position on that pool is released), and assert after every call that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active.
