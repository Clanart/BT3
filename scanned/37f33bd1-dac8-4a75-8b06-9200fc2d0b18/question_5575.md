# Q5575: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
wombat/WombatBribeManager.sol: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. With _lp and the moment the whole position on that pool is released under attacker control and the attacker passes an lp address that was never registered in poolInfos, can an unprivileged caller sequence `unvote(address _lp)` so that `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` no longer reconcile, violating the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes an lp address that was never registered in poolInfos, have the attacker run `unvote(address _lp)`, then assert the victim's claimable value and the `totalVlMgpInVote` versus `sum of userTotalVotedInVlmgp over all voters` relation are unchanged by the attacker's transaction.
