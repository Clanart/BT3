# Q2911: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Under the attacker votes in the block immediately before a known keeper cast, is there an unprivileged sequence of `harvestSinglePool(address[] _lps)` that leaves `poolInfos[lp].totalVoteInVlmgp` unreconciled with `totalVlMgpInVote`, violates the invariant that only registered pools may be forwarded into the voter and the reward queue, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that only registered pools may be forwarded into the voter and the reward queue.
