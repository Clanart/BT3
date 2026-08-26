# Q1191: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `claimAllBribes(address _for)` that leaves `delegatedPool votes` unreconciled with `totalVlMgpInVote`, violates the invariant that a full settlement across every pool must be initiated by the position owner, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claimAllBribes(address _for)` sequence atomically under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting at the end that `delegatedPool votes` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
