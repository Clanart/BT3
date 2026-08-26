# Q5550: WombatBribeManager.vote - getUserVotable ignores balances in cooldown

## Question
Note that in wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under the attacker passes an lp address that was never registered in poolInfos and force `earnedRewards reported by claimAllBribes` apart from `the tokens actually transferred by getReward`, breaking the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes an lp address that was never registered in poolInfos, call `vote(address[] _lps, int256[] _deltas)`, and assert `earnedRewards reported by claimAllBribes` equals `the tokens actually transferred by getReward` and that no account can withdraw more than it put in.
