# Q5855: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Under the victim has a large unsettled balance in the pool rewarder, is there an unprivileged sequence of `claimAllBribes(address _for)` that leaves `earnedRewards reported by claimAllBribes` unreconciled with `the tokens actually transferred by getReward`, violates the invariant that a full settlement across every pool must be initiated by the position owner, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claimAllBribes(address _for)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them), and assert after every call that a full settlement across every pool must be initiated by the position owner.
