# Q0496: SignedSafeMath.wdiv - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. With the target minus current arithmetic inside WombatBribeManager.castVotes under attacker control and the voter supplies the same pool several times with alternating signs, can an unprivileged caller sequence `wdiv(int256 x, int256 y)` so that `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` no longer reconcile, violating the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the target minus current arithmetic inside WombatBribeManager.castVotes) under the voter supplies the same pool several times with alternating signs, asserting on every row that a signed input must be validated against the unsigned counter it will mutate before the cast.
