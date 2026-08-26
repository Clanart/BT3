# Q2892: WombatBribeManager.harvestSinglePool - harvestSinglePool drains pending bribes with no caller fee

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Starting from a state where the attacker votes in the block immediately before a known keeper cast, can an unprivileged EOA use `harvestSinglePool(address[] _lps)` to leave `userTotalVotedInVlmgp[msg.sender]` inconsistent with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violating the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes in the block immediately before a known keeper cast, snapshot `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, run the attacker's `harvestSinglePool(address[] _lps)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
