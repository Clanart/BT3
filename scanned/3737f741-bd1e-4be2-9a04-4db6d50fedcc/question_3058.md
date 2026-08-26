# Q3058: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
Consider wombat/WombatBribeManager.sol, where claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Assuming the attacker votes in the block immediately before a known keeper cast, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` via `claimAllBribes(address _for)`, breaking the invariant that a full settlement across every pool must be initiated by the position owner and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claimAllBribes(address _for)` sequence atomically under the attacker votes in the block immediately before a known keeper cast, asserting at the end that `userTotalVotedInVlmgp[msg.sender]` still equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the PoC's balance delta is non-positive.
