# Q5849: WombatBribeManager.claimAllBribes - offsetting deltas keep the net total unchanged

## Question
wombat/WombatBribeManager.sol: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Under the victim has a large unsettled balance in the pool rewarder, is there an unprivileged sequence of `claimAllBribes(address _for)` that leaves `delegatedPool votes` unreconciled with `totalVlMgpInVote`, violates the invariant that a rebalancing vote must be validated against the real per-pool positions it creates, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unsettled balance in the pool rewarder, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `claimAllBribes(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
