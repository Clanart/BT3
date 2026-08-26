# Q5660: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker reach this through `claimBribe(address[] lps)` while the attacker passes an lp address that was never registered in poolInfos, and drive `userVotedForPoolInVlmgp[user][lp]` out of agreement with `IBribeRewardPool(pool.rewarder).balanceOf(user)` - breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes an lp address that was never registered in poolInfos, have the attacker run `claimBribe(address[] lps)`, then assert the victim's claimable value and the `userVotedForPoolInVlmgp[user][lp]` versus `IBribeRewardPool(pool.rewarder).balanceOf(user)` relation are unchanged by the attacker's transaction.
