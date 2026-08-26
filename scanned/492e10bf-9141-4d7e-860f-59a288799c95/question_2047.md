# Q2047: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
In wombat/WombatBribeManager.sol, the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while the attacker locks vlMGP, votes and casts inside a single transaction, and drive `poolInfos[lp].totalVoteInVlmgp` out of agreement with `totalVlMgpInVote` - breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks vlMGP, votes and casts inside a single transaction, then assert `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` end identical in both runs.
