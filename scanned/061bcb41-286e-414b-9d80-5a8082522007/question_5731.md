# Q5731: WombatBribeManager.castVotes - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol - because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker controlling the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination, under the bribe contract for the pool registers more than one reward token, exploit this through `castVotes(bool swapForBnb)` to break the reconciliation between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` and the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the bribe contract for the pool registers more than one reward token, snapshot `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
