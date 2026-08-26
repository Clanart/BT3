# Q2392: WombatBribeManager.vote - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Under the attacker votes in the block immediately before a known keeper cast, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `poolInfos[lp].totalVoteInVlmgp` unreconciled with `totalVlMgpInVote`, violates the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes in the block immediately before a known keeper cast, snapshot `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
