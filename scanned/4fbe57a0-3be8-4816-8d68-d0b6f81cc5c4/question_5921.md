# Q5921: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. With the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0) under attacker control and the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged caller sequence `harvestSinglePool(address[] _lps)` so that `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` no longer reconcile, violating the invariant that only registered pools may be forwarded into the voter and the reward queue and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `harvestSinglePool(address[] _lps)`, and assert `poolInfos[lp].totalVoteInVlmgp` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
