# Q0961: SignedSafeMath.toUint256 - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. With the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote under attacker control and targetVote is above currentVote so the first branch of castVotes runs, can an unprivileged caller sequence `toUint256(int256 value)` so that `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` no longer reconcile, violating the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `toUint256(int256 value)` sequence atomically under targetVote is above currentVote so the first branch of castVotes runs, asserting at the end that `int256(targetVote) - int256(currentVote)` still equals `the uint256 votes pushed into the Wombat voter` and the PoC's balance delta is non-positive.
