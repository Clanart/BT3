# Q5644: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Under the attacker passes an lp address that was never registered in poolInfos, is there an unprivileged sequence of `harvestSinglePool(address[] _lps)` that leaves `totalVlMgpInVote` unreconciled with `sum of userTotalVotedInVlmgp over all voters`, violates the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes an lp address that was never registered in poolInfos, then assert `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` end identical in both runs.
