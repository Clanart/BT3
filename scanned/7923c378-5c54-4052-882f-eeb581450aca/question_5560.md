# Q5560: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Starting from a state where the attacker passes an lp address that was never registered in poolInfos, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `totalVlMgpInVote` inconsistent with `sum of userTotalVotedInVlmgp over all voters`, violating the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes an lp address that was never registered in poolInfos, snapshot `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
