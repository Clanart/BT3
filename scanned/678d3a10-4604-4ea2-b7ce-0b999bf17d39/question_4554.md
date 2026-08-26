# Q4554: WombatBribeManager.unvote - offsetting deltas keep the net total unchanged

## Question
Consider wombat/WombatBribeManager.sol, where because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Assuming delegatedPool is unset so the delegate legs are skipped, can an unprivileged attacker turn this into a divergence between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` via `unvote(address _lp)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `unvote(address _lp)`: constrain the setup so that delegatedPool is unset so the delegate legs are skipped, fuzz the attacker inputs (_lp and the moment the whole position on that pool is released), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
