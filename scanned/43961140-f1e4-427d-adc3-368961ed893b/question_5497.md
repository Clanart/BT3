# Q5497: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. With _for (any victim) and the block at which every pool rewarder is settled for them under attacker control and the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged caller sequence `claimAllBribes(address _for)` so that `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` no longer reconcile, violating the invariant that a full settlement across every pool must be initiated by the position owner and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker passes offsetting positive and negative deltas that net to zero, have the attacker run `claimAllBribes(address _for)`, then assert the victim's claimable value and the `getVoteForLp(lp) from the Wombat voter` versus `poolInfos[lp].totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
