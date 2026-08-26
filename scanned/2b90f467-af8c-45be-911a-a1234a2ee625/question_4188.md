# Q4188: WombatBribeManager.voteAndCast - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, is there an unprivileged sequence of `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` that leaves `delegatedPool votes` unreconciled with `totalVlMgpInVote`, violates the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
