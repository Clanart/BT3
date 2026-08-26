# Q1233: SignedSafeMath.toUint256 - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. With the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote under attacker control and the delegated pool's votes are included in the pool total but not in the denominator, can an unprivileged caller sequence `toUint256(int256 value)` so that `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` no longer reconcile, violating the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the delegated pool's votes are included in the pool total but not in the denominator.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `toUint256(int256 value)` sequence atomically under the delegated pool's votes are included in the pool total but not in the denominator, asserting at the end that `totalUserVote accumulated as int256` still equals `userTotalVotedInVlmgp as uint256` and the PoC's balance delta is non-positive.
