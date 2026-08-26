# Q5461: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With the lp array and the settlement timing under attacker control and the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged caller sequence `claimBribe(address[] lps)` so that `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes offsetting positive and negative deltas that net to zero, call `claimBribe(address[] lps)`, and assert `totalVlMgpInVote` equals `sum of userTotalVotedInVlmgp over all voters` and that no account can withdraw more than it put in.
