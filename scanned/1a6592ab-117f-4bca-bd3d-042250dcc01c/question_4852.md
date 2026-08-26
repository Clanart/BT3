# Q4852: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol - claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Can an unprivileged attacker controlling _for (any victim) and the block at which every pool rewarder is settled for them, under delegatedPool is unset so the delegate legs are skipped, exploit this through `claimAllBribes(address _for)` to break the reconciliation between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the invariant that a full settlement across every pool must be initiated by the position owner, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claimAllBribes(address _for)`: constrain the setup so that delegatedPool is unset so the delegate legs are skipped, fuzz the attacker inputs (_for (any victim) and the block at which every pool rewarder is settled for them), and assert after every call that a full settlement across every pool must be initiated by the position owner.
