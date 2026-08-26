# Q2139: WombatBribeManager.claimAllBribes - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. With _for (any victim) and the block at which every pool rewarder is settled for them under attacker control and the attacker locks vlMGP, votes and casts inside a single transaction, can an unprivileged caller sequence `claimAllBribes(address _for)` so that `delegatedPool votes` and `totalVlMgpInVote` no longer reconcile, violating the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks vlMGP, votes and casts inside a single transaction, have the attacker run `claimAllBribes(address _for)`, then assert the victim's claimable value and the `delegatedPool votes` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
