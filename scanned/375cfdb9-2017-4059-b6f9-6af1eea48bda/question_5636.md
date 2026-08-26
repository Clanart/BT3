# Q5636: WombatBribeManager.voteAndCast - offsetting deltas keep the net total unchanged

## Question
Note that in wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an attacker holding only tokens bought on market reach it via `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` under the attacker passes an lp address that was never registered in poolInfos and force `poolInfos[lp].totalVoteInVlmgp` apart from `totalVlMgpInVote`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`: constrain the setup so that the attacker passes an lp address that was never registered in poolInfos, fuzz the attacker inputs (the deltas and the atomic vote-then-cast ordering inside one transaction), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
