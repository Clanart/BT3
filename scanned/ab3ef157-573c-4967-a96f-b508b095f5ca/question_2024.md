# Q2024: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while the attacker locks vlMGP, votes and casts inside a single transaction, and drive `userTotalVotedInVlmgp[msg.sender]` out of agreement with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` - breaking the invariant that only registered pools may be forwarded into the voter and the reward queue - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locks vlMGP, votes and casts inside a single transaction, snapshot `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, run the attacker's `harvestSinglePool(address[] _lps)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
