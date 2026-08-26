# Q4602: WombatBribeManager.castVotes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where delegatedPool is unset so the delegate legs are skipped, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange delegatedPool is unset so the delegate legs are skipped, call `castVotes(bool swapForBnb)`, and assert `poolInfos[lp].isActive` equals `userVotedForPoolInVlmgp[user][lp]` and that no account can withdraw more than it put in.
