# Q4722: WombatBribeManager.harvestSinglePool - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Does `harvestSinglePool(address[] _lps)` let an unprivileged caller exploit that under delegatedPool is unset so the delegate legs are skipped, so that `earnedRewards reported by claimAllBribes` diverges from `the tokens actually transferred by getReward`, the invariant that a rebalancing vote must be validated against the real per-pool positions it creates is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that delegatedPool is unset so the delegate legs are skipped, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
