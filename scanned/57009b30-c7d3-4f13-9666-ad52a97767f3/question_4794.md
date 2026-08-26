# Q4794: WombatBribeManager.claimBribeFor - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Under delegatedPool is unset so the delegate legs are skipped, is there an unprivileged sequence of `claimBribeFor(address[] lps, address _for)` that leaves `poolInfos[lp].totalVoteInVlmgp` unreconciled with `totalVlMgpInVote`, violates the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `claimBribeFor(address[] lps, address _for)` sequence atomically under delegatedPool is unset so the delegate legs are skipped, asserting at the end that `poolInfos[lp].totalVoteInVlmgp` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
