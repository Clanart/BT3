# Q5648: WombatBribeManager.harvestSinglePool - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol - getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the attacker passes an lp address that was never registered in poolInfos, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes an lp address that was never registered in poolInfos, call `harvestSinglePool(address[] _lps)`, and assert `userVotedForPoolInVlmgp[user][lp]` equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and that no account can withdraw more than it put in.
