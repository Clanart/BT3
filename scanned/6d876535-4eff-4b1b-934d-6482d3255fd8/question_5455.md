# Q5455: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts duplicate lp addresses

## Question
In wombat/WombatBribeManager.sol, the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while the attacker passes offsetting positive and negative deltas that net to zero, and drive `delegatedPool votes` out of agreement with `totalVlMgpInVote` - breaking the invariant that harvesting one pool twice in a call must be equivalent to harvesting it once - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts duplicate lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: the array is iterated without a uniqueness check, so the same pool can be harvested repeatedly inside one call while the reward measurement in WombatStaking is taken as a balance delta. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: harvesting one pool twice in a call must be equivalent to harvesting it once; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes offsetting positive and negative deltas that net to zero, call `harvestSinglePool(address[] _lps)`, and assert `delegatedPool votes` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
