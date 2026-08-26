# Q5969: WombatBribeManager.unvote - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With _lp and the moment the whole position on that pool is released under attacker control and a keeper castVotes transaction is pending in the mempool, can an unprivileged caller sequence `unvote(address _lp)` so that `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a keeper castVotes transaction is pending in the mempool, then assert `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` end identical in both runs.
