# Q5777: WombatBribeManager.castVotesAndClaimBribes - getUserVotable ignores balances in cooldown

## Question
wombat/WombatBribeManager.sol: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. With the cast and the immediately following claim inside one transaction under attacker control and the bribe contract for the pool registers more than one reward token, can an unprivileged caller sequence `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` so that `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` no longer reconcile, violating the invariant that the voting ceiling and the votes already cast must be reconciled on every change to the locked balance and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: getUserVotable ignores balances in cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: getUserVotable() returns IVLMGP(vlMGP).getUserTotalLocked(_user), which excludes cooldown slots, so the ceiling shifts whenever the user starts or cancels a cooldown, and cancelUnlock raises it with no revalidation of existing votes. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the voting ceiling and the votes already cast must be reconciled on every change to the locked balance; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the bribe contract for the pool registers more than one reward token, have the attacker run `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, then assert the victim's claimable value and the `earnedRewards reported by claimAllBribes` versus `the tokens actually transferred by getReward` relation are unchanged by the attacker's transaction.
