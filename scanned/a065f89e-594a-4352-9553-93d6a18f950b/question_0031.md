# Q0031: SignedSafeMath.wmul - signed deltas are cast into unsigned counters

## Question
Note that in libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an attacker holding only tokens bought on market reach it via `wmul(int256 x, int256 y)` under the voter supplies offsetting positive and negative deltas that net to zero and force `int256 delta supplied by the voter` apart from `uint256 pool.totalVoteInVlmgp`, breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `wmul(int256 x, int256 y)`: constrain the setup so that the voter supplies offsetting positive and negative deltas that net to zero, fuzz the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at), and assert after every call that a signed input must be validated against the unsigned counter it will mutate before the cast.
