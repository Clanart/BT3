# Q5654: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
Note that in wombat/WombatBribeManager.sol, harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Can an attacker holding only tokens bought on market reach it via `harvestSinglePool(address[] _lps)` under the attacker passes an lp address that was never registered in poolInfos and force `delegatedPool votes` apart from `totalVlMgpInVote`, breaking the invariant that only registered pools may be forwarded into the voter and the reward queue for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvestSinglePool(address[] _lps)` sequence atomically under the attacker passes an lp address that was never registered in poolInfos, asserting at the end that `delegatedPool votes` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
