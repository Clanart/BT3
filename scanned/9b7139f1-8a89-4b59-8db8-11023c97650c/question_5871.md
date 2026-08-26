# Q5871: WombatBribeManager.vote - offsetting deltas keep the net total unchanged

## Question
Note that in wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under the attacker has just cancelled a cooldown so getUserVotable jumped upward and force `poolInfos[lp].totalVoteInVlmgp` apart from `totalVlMgpInVote`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `vote(address[] _lps, int256[] _deltas)`: constrain the setup so that the attacker has just cancelled a cooldown so getUserVotable jumped upward, fuzz the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
