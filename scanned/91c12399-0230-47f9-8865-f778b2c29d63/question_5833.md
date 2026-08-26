# Q5833: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol - because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the victim has a large unsettled balance in the pool rewarder, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled balance in the pool rewarder, have the attacker run `harvestSinglePool(address[] _lps)`, then assert the victim's claimable value and the `targetVote computed in castVotes` versus `totalVotes() from veWom.balanceOf(wombatStaking)` relation are unchanged by the attacker's transaction.
