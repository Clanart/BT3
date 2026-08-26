# Q5931: WombatBribeManager.claimAllBribes - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With _for (any victim) and the block at which every pool rewarder is settled for them under attacker control and the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged caller sequence `claimAllBribes(address _for)` so that `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them) under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting on every row that a rebalancing vote must be validated against the real per-pool positions it creates.
