# Q4973: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an unprivileged attacker reach this through `vote(address[] _lps, int256[] _deltas)` while the attacker passes the same lp address several times in one array, and drive `userTotalVotedInVlmgp[msg.sender]` out of agreement with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` - breaking the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker passes the same lp address several times in one array, asserting on every row that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge.
