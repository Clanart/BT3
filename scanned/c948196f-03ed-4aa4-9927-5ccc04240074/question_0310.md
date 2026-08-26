# Q0310: SignedSafeMath.wmul - signed deltas are cast into unsigned counters

## Question
In libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Does `wmul(int256 x, int256 y)` let an unprivileged caller exploit that under the voter supplies the same pool several times with alternating signs, so that `int256(targetVote) - int256(currentVote)` diverges from `the uint256 votes pushed into the Wombat voter`, the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `wmul(int256 x, int256 y)`: constrain the setup so that the voter supplies the same pool several times with alternating signs, fuzz the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at), and assert after every call that a signed input must be validated against the unsigned counter it will mutate before the cast.
