# Q5681: WombatBribeManager.castVotesAndClaimBribes - offsetting deltas keep the net total unchanged

## Question
In wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an unprivileged attacker reach this through `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` while the attacker passes an lp address that was never registered in poolInfos, and drive `poolInfos[lp].isActive` out of agreement with `userVotedForPoolInVlmgp[user][lp]` - breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes an lp address that was never registered in poolInfos, have the attacker run `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, then assert the victim's claimable value and the `poolInfos[lp].isActive` versus `userVotedForPoolInVlmgp[user][lp]` relation are unchanged by the attacker's transaction.
