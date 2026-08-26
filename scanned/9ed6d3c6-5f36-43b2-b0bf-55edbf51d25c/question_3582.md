# Q3582: WombatBribeManager.harvestSinglePool - getUserVotable ignores balances in cooldown

## Question
Consider wombat/WombatBribeManager.sol, where getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Assuming the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged attacker turn this into a divergence between `delegatedPool votes` and `totalVlMgpInVote` via `harvestSinglePool(address[] _lps)`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that the pool the attacker voted for has been deactivated so unvote reverts, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
