# Q0884: WombatBribeManager.harvestSinglePool - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker reach this through `harvestSinglePool(address[] _lps)` while a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, and drive `targetVote computed in castVotes` out of agreement with `totalVotes() from veWom.balanceOf(wombatStaking)` - breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, then assert `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` end identical in both runs.
