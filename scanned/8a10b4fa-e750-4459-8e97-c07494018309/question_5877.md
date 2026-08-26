# Q5877: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an unprivileged attacker reach this through `vote(address[] _lps, int256[] _deltas)` while the attacker has just cancelled a cooldown so getUserVotable jumped upward, and drive `getVoteForLp(lp) from the Wombat voter` out of agreement with `poolInfos[lp].totalVoteInVlmgp` - breaking the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has just cancelled a cooldown so getUserVotable jumped upward, have the attacker run `vote(address[] _lps, int256[] _deltas)`, then assert the victim's claimable value and the `getVoteForLp(lp) from the Wombat voter` versus `poolInfos[lp].totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
