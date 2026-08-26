# Q5143: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
Consider wombat/WombatBribeManager.sol, where because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Assuming the attacker passes the same lp address several times in one array, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` via `harvestSinglePool(address[] _lps)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `harvestSinglePool(address[] _lps)` sequence atomically under the attacker passes the same lp address several times in one array, asserting at the end that `userTotalVotedInVlmgp[msg.sender]` still equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the PoC's balance delta is non-positive.
