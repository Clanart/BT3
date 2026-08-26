# Q5751: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
Consider wombat/WombatBribeManager.sol, where because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Assuming the bribe contract for the pool registers more than one reward token, can an unprivileged attacker turn this into a divergence between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` via `harvestSinglePool(address[] _lps)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under the bribe contract for the pool registers more than one reward token, asserting on every row that a rebalancing vote must be validated against the real per-pool positions it creates.
