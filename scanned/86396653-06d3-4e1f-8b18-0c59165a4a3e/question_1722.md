# Q1722: WombatBribeManager.castVotes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where the attacker locks vlMGP, votes and casts inside a single transaction, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `totalVlMgpInVote` inconsistent with `sum of userTotalVotedInVlmgp over all voters`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker locks vlMGP, votes and casts inside a single transaction, call `castVotes(bool swapForBnb)`, and assert `totalVlMgpInVote` equals `sum of userTotalVotedInVlmgp over all voters` and that no account can withdraw more than it put in.
