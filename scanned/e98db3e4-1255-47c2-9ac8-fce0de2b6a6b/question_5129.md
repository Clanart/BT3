# Q5129: WombatBribeManager.voteAndCast - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where the attacker passes the same lp address several times in one array, can an unprivileged EOA use `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` to leave `earnedRewards reported by claimAllBribes` inconsistent with `the tokens actually transferred by getReward`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence atomically under the attacker passes the same lp address several times in one array, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
