# Q0543: WombatBribeManager.castVotes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, and drive `poolInfos[lp].totalVoteInVlmgp` out of agreement with `totalVlMgpInVote` - breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting at the end that `poolInfos[lp].totalVoteInVlmgp` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
