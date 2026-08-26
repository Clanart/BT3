# Q5164: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
wombat/WombatBribeManager.sol - harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the attacker passes the same lp address several times in one array, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` and the invariant that only registered pools may be forwarded into the voter and the reward queue, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that the attacker passes the same lp address several times in one array, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that only registered pools may be forwarded into the voter and the reward queue.
