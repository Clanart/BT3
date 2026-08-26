# Q5937: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. With _for (any victim) and the block at which every pool rewarder is settled for them under attacker control and the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged caller sequence `claimAllBribes(address _for)` so that `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` no longer reconcile, violating the invariant that a full settlement across every pool must be initiated by the position owner and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `claimAllBribes(address _for)`, and assert `userTotalVotedInVlmgp[msg.sender]` equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and that no account can withdraw more than it put in.
