# Q4268: WombatBribeManager.claimBribe - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker reach this through `claimBribe(address[] lps)` while the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, and drive `earnedRewards reported by claimAllBribes` out of agreement with `the tokens actually transferred by getReward` - breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribe(address[] lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribe(address[] lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array and the settlement timing
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `claimBribe(address[] lps)`: constrain the setup so that the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, fuzz the attacker inputs (the lp array and the settlement timing), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
