# Q0682: SignedSafeMath.toUint256 - signed deltas are cast into unsigned counters

## Question
Consider libraries/SignedSafeMath.sol, where WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Assuming targetVote is below currentVote so the second branch of castVotes runs, can an unprivileged attacker turn this into a divergence between `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` via `toUint256(int256 value)`, breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `toUint256(int256 value)` sequence atomically under targetVote is below currentVote so the second branch of castVotes runs, asserting at the end that `int256 delta supplied by the voter` still equals `uint256 pool.totalVoteInVlmgp` and the PoC's balance delta is non-positive.
