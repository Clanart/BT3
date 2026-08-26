# Q2930: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
In wombat/WombatBribeManager.sol, the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Starting from a state where the attacker votes in the block immediately before a known keeper cast, can an unprivileged EOA use `harvestSinglePool(address[] _lps)` to leave `totalVlMgpInVote` inconsistent with `sum of userTotalVotedInVlmgp over all voters`, violating the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker votes in the block immediately before a known keeper cast, have the attacker run `harvestSinglePool(address[] _lps)`, then assert the victim's claimable value and the `totalVlMgpInVote` versus `sum of userTotalVotedInVlmgp over all voters` relation are unchanged by the attacker's transaction.
