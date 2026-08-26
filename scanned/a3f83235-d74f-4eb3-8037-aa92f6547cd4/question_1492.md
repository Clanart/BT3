# Q1492: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Under the attacker locks vlMGP, votes and casts inside a single transaction, is there an unprivileged sequence of `vote(address[] _lps, int256[] _deltas)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker locks vlMGP, votes and casts inside a single transaction, asserting on every row that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge.
