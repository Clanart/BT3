# Q3662: WombatBribeManager.claimBribeFor - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `claimBribeFor(address[] lps, address _for)` to leave `earnedRewards reported by claimAllBribes` inconsistent with `the tokens actually transferred by getReward`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `claimBribeFor(address[] lps, address _for)` sequence atomically under the pool the attacker voted for has been deactivated so unvote reverts, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
