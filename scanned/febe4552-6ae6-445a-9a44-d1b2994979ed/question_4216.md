# Q4216: WombatBribeManager.harvestSinglePool - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol - getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an unprivileged attacker controlling the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0), under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, exploit this through `harvestSinglePool(address[] _lps)` to break the reconciliation between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` and the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, snapshot `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward`, run the attacker's `harvestSinglePool(address[] _lps)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
