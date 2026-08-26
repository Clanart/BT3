# Q2208: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Can an unprivileged attacker reach this through `claimAllBribes(address _for)` while the attacker locks vlMGP, votes and casts inside a single transaction, and drive `earnedRewards reported by claimAllBribes` out of agreement with `the tokens actually transferred by getReward` - breaking the invariant that a full settlement across every pool must be initiated by the position owner - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locks vlMGP, votes and casts inside a single transaction, snapshot `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward`, run the attacker's `claimAllBribes(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
