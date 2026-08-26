# Q5759: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
In wombat/WombatBribeManager.sol, the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while the bribe contract for the pool registers more than one reward token, and drive `userTotalVotedInVlmgp[msg.sender]` out of agreement with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` - breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under the bribe contract for the pool registers more than one reward token, asserting on every row that harvesting one pool twice in a call must be equivalent to harvesting it once.
