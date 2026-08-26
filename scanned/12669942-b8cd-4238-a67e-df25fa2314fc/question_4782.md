# Q4782: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
Consider wombat/WombatBribeManager.sol, where because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Assuming delegatedPool is unset so the delegate legs are skipped, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` via `claimBribe(address[] lps)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array and the settlement timing) under delegatedPool is unset so the delegate legs are skipped, asserting on every row that a rebalancing vote must be validated against the real per-pool positions it creates.
