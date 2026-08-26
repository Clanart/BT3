# Q5841: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
Consider wombat/WombatBribeManager.sol, where the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Assuming the victim has a large unsettled balance in the pool rewarder, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` via `harvestSinglePool(address[] _lps)`, breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled balance in the pool rewarder, have the attacker run `harvestSinglePool(address[] _lps)`, then assert the victim's claimable value and the `poolInfos[lp].totalVoteInVlmgp` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
