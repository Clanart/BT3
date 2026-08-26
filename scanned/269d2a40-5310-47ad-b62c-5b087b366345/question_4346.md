# Q4346: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
Consider wombat/WombatBribeManager.sol, where claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Assuming the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, can an unprivileged attacker turn this into a divergence between `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` via `claimAllBribes(address _for)`, breaking the invariant that a full settlement across every pool must be initiated by the position owner and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them) under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, asserting on every row that a full settlement across every pool must be initiated by the position owner.
