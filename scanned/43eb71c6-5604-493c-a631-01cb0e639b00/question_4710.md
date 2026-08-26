# Q4710: WombatBribeManager.voteAndCast - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. With the deltas and the atomic vote-then-cast ordering inside one transaction under attacker control and delegatedPool is unset so the delegate legs are skipped, can an unprivileged caller sequence `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` so that `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` no longer reconcile, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the deltas and the atomic vote-then-cast ordering inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish delegatedPool is unset so the delegate legs are skipped, have the attacker run `voteAndCast(address[] _lps, int256[] _deltas, bool swapForBnb)`, then assert the victim's claimable value and the `earnedRewards reported by claimAllBribes` versus `the tokens actually transferred by getReward` relation are unchanged by the attacker's transaction.
