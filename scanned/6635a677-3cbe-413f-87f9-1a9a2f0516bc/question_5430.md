# Q5430: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0) under attacker control and the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged caller sequence `harvestSinglePool(address[] _lps)` so that `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes offsetting positive and negative deltas that net to zero, have the attacker run `harvestSinglePool(address[] _lps)`, then assert the victim's claimable value and the `poolInfos[lp].totalVoteInVlmgp` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
