# Q2854: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0) under attacker control and the attacker votes in the block immediately before a known keeper cast, can an unprivileged caller sequence `harvestSinglePool(address[] _lps)` so that `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes in the block immediately before a known keeper cast, snapshot `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp`, run the attacker's `harvestSinglePool(address[] _lps)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
