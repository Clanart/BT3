# Q5283: WombatBribeManager.vote - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Starting from a state where the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `delegatedPool votes` inconsistent with `totalVlMgpInVote`, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (every lp address and every signed delta, including duplicates and offsetting positive and negative entries) under the attacker passes offsetting positive and negative deltas that net to zero, asserting on every row that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance.
