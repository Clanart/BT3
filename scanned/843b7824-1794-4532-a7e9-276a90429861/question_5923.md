# Q5923: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
wombat/WombatBribeManager.sol - the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the attacker has just cancelled a cooldown so getUserVotable jumped upward, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` and the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvestSinglePool(address[] _lps)` sequence atomically under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting at the end that `totalVlMgpInVote` still equals `sum of userTotalVotedInVlmgp over all voters` and the PoC's balance delta is non-positive.
