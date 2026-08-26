# Q5663: WombatBribeManager.claimBribeFor - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol - because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker controlling _for (any victim) and the lp array, under the attacker passes an lp address that was never registered in poolInfos, exploit this through `claimBribeFor(address[] lps, address _for)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes an lp address that was never registered in poolInfos, call `claimBribeFor(address[] lps, address _for)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
