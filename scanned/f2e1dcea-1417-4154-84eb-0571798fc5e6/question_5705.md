# Q5705: WombatBribeManager.vote - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and the bribe contract for the pool registers more than one reward token, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` no longer reconcile, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` end identical in both runs.
