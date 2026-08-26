# Q0946: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
Consider wombat/WombatBribeManager.sol, where harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Assuming a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged attacker turn this into a divergence between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` via `harvestSinglePool(address[] _lps)`, breaking the invariant that only registered pools may be forwarded into the voter and the reward queue and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)) under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting on every row that only registered pools may be forwarded into the voter and the reward queue.
