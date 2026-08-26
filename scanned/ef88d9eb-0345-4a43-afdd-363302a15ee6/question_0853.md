# Q0853: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, and drive `userVotedForPoolInVlmgp[user][lp]` out of agreement with `IBribeRewardPool(pool.rewarder).balanceOf(user)` - breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting on every row that a rebalancing vote must be validated against the real per-pool positions it creates.
