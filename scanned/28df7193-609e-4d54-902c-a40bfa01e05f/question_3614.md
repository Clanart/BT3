# Q3614: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
Note that in wombat/WombatBribeManager.sol, harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Can an attacker holding only tokens bought on market reach it via `harvestSinglePool(address[] _lps)` under the pool the attacker voted for has been deactivated so unvote reverts and force `totalVlMgpInVote` apart from `sum of userTotalVotedInVlmgp over all voters`, breaking the invariant that only registered pools may be forwarded into the voter and the reward queue for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool the attacker voted for has been deactivated so unvote reverts, then assert `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` end identical in both runs.
