# Q0124: SignedSafeMath.toUint256 - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol - WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an unprivileged attacker controlling the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote, under the voter supplies offsetting positive and negative deltas that net to zero, exploit this through `toUint256(int256 value)` to break the reconciliation between `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` and the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast, yielding Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `toUint256(int256 value)` sequence atomically under the voter supplies offsetting positive and negative deltas that net to zero, asserting at the end that `int256(targetVote) - int256(currentVote)` still equals `the uint256 votes pushed into the Wombat voter` and the PoC's balance delta is non-positive.
