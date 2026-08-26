# Q5887: WombatBribeManager.unvote - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Under the attacker has just cancelled a cooldown so getUserVotable jumped upward, is there an unprivileged sequence of `unvote(address _lp)` that leaves `totalVlMgpInVote` unreconciled with `sum of userTotalVotedInVlmgp over all voters`, violates the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `unvote(address _lp)` sequence atomically under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting at the end that `totalVlMgpInVote` still equals `sum of userTotalVotedInVlmgp over all voters` and the PoC's balance delta is non-positive.
