# Q5604: WombatBribeManager.castVotes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the attacker passes an lp address that was never registered in poolInfos, so that `userTotalVotedInVlmgp[msg.sender]` diverges from `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, the invariant that a rebalancing vote must be validated against the real per-pool positions it creates is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes an lp address that was never registered in poolInfos, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp[msg.sender]` versus `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` relation are unchanged by the attacker's transaction.
