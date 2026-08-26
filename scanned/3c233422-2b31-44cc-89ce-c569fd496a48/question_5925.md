# Q5925: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With the lp array and the settlement timing under attacker control and the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged caller sequence `claimBribe(address[] lps)` so that `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has just cancelled a cooldown so getUserVotable jumped upward, snapshot `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]`, run the attacker's `claimBribe(address[] lps)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
