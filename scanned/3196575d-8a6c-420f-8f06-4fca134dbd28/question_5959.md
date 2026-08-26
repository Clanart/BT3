# Q5959: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
Consider wombat/WombatBribeManager.sol, where harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Assuming a keeper castVotes transaction is pending in the mempool, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under a keeper castVotes transaction is pending in the mempool, asserting at the end that `poolInfos[lp].isActive` still equals `userVotedForPoolInVlmgp[user][lp]` and the PoC's balance delta is non-positive.
