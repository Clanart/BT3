# Q3742: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
Note that in wombat/WombatBribeManager.sol, claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Can an attacker holding only tokens bought on market reach it via `claimAllBribes(address _for)` under the pool the attacker voted for has been deactivated so unvote reverts and force `poolInfos[lp].totalVoteInVlmgp` apart from `totalVlMgpInVote`, breaking the invariant that a full settlement across every pool must be initiated by the position owner for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool the attacker voted for has been deactivated so unvote reverts, then assert `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` end identical in both runs.
