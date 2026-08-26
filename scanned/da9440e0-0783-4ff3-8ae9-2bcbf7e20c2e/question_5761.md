# Q5761: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Does `claimBribe(address[] lps)` let an unprivileged caller exploit that under the bribe contract for the pool registers more than one reward token, so that `targetVote computed in castVotes` diverges from `totalVotes() from veWom.balanceOf(wombatStaking)`, the invariant that a rebalancing vote must be validated against the real per-pool positions it creates is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `claimBribe(address[] lps)`: constrain the setup so that the bribe contract for the pool registers more than one reward token, fuzz the attacker inputs (the lp array and the settlement timing), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
