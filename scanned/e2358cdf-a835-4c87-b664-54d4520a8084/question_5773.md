# Q5773: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Does `claimAllBribes(address _for)` let an unprivileged caller exploit that under the bribe contract for the pool registers more than one reward token, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a full settlement across every pool must be initiated by the position owner is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `delegatedPool votes` and `totalVlMgpInVote` end identical in both runs.
