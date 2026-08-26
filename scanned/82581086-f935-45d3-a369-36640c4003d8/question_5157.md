# Q5157: WombatBribeManager.harvestSinglePool - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol - harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the attacker passes the same lp address several times in one array, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes the same lp address several times in one array, call `harvestSinglePool(address[] _lps)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
