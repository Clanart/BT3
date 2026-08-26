# Q4281: WombatBribeManager.claimBribeFor - offsetting deltas keep the net total unchanged

## Question
Note that in wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an attacker holding only tokens bought on market reach it via `claimBribeFor(address[] lps, address _for)` under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp and force `userTotalVotedInVlmgp[msg.sender]` apart from `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, call `claimBribeFor(address[] lps, address _for)`, and assert `userTotalVotedInVlmgp[msg.sender]` equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and that no account can withdraw more than it put in.
