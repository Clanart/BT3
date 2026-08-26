# Q3331: WombatBribeManager.unvote - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `unvote(address _lp)` to leave `userVotedForPoolInVlmgp[user][lp]` inconsistent with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `unvote(address _lp)`: constrain the setup so that the pool the attacker voted for has been deactivated so unvote reverts, fuzz the attacker inputs (_lp and the moment the whole position on that pool is released), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
