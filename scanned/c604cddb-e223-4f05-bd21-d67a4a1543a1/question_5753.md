# Q5753: WombatBribeManager.harvestSinglePool - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Does `harvestSinglePool(address[] _lps)` let an unprivileged caller exploit that under the bribe contract for the pool registers more than one reward token, so that `targetVote computed in castVotes` diverges from `totalVotes() from veWom.balanceOf(wombatStaking)`, the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `harvestSinglePool(address[] _lps)`: constrain the setup so that the bribe contract for the pool registers more than one reward token, fuzz the attacker inputs (the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)), and assert after every call that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
