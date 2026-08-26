# Q0791: WombatBribeManager.voteAndCast - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With the deltas and the atomic vote-then-cast ordering inside one transaction under attacker control and a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged caller sequence `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` so that `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, snapshot `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters`, run the attacker's `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
