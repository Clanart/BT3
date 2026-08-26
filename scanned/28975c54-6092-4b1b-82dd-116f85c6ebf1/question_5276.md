# Q5276: WombatBribeManager.vote - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes offsetting positive and negative deltas that net to zero, have the attacker run `vote(address[] _lps, int256[] _deltas)`, then assert the victim's claimable value and the `poolInfos[lp].isActive` versus `userVotedForPoolInVlmgp[user][lp]` relation are unchanged by the attacker's transaction.
