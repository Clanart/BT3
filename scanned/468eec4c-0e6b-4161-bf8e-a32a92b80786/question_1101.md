# Q1101: WombatBribeManager.claimAllBribes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Starting from a state where a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged EOA use `claimAllBribes(address _for)` to leave `poolInfos[lp].isActive` inconsistent with `userVotedForPoolInVlmgp[user][lp]`, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `claimAllBribes(address _for)`: constrain the setup so that a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, fuzz the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them), and assert after every call that a rebalancing vote must be validated against the real per-pool positions it creates.
