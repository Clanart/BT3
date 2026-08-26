# Q1317: SignedSafeMath.wdiv - signed deltas are cast into unsigned counters

## Question
Note that in libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an attacker holding only tokens bought on market reach it via `wdiv(int256 x, int256 y)` under the delegated pool's votes are included in the pool total but not in the denominator and force `int256 delta supplied by the voter` apart from `uint256 pool.totalVoteInVlmgp`, breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the delegated pool's votes are included in the pool total but not in the denominator.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `wdiv(int256 x, int256 y)` sequence atomically under the delegated pool's votes are included in the pool total but not in the denominator, asserting at the end that `int256 delta supplied by the voter` still equals `uint256 pool.totalVoteInVlmgp` and the PoC's balance delta is non-positive.
