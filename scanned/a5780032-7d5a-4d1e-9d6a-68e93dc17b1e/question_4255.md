# Q4255: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
In wombat/WombatBribeManager.sol, the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, and drive `targetVote computed in castVotes` out of agreement with `totalVotes() from veWom.balanceOf(wombatStaking)` - breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that harvesting one pool twice in a call must be equivalent to harvesting it once.
