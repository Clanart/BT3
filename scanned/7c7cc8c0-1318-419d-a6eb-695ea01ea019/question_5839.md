# Q5839: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Under the victim has a large unsettled balance in the pool rewarder, is there an unprivileged sequence of `harvestSinglePool(address[] _lps)` that leaves `userTotalVotedInVlmgp[msg.sender]` unreconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violates the invariant that only registered pools may be forwarded into the voter and the reward queue, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that only registered pools may be forwarded into the voter and the reward queue.
