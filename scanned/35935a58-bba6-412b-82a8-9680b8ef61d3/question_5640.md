# Q5640: WombatBribeManager.voteAndCast - getUserVotable ignores balances in cooldown

## Question
In wombat/WombatBribeManager.sol, getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Starting from a state where the attacker passes an lp address that was never registered in poolInfos, can an unprivileged EOA use `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` to leave `totalVlMgpInVote` inconsistent with `sum of userTotalVotedInVlmgp over all voters`, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes an lp address that was never registered in poolInfos, snapshot `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters`, run the attacker's `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
