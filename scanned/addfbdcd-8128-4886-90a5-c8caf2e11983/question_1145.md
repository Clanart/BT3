# Q1145: SignedSafeMath.wmul - signed deltas are cast into unsigned counters

## Question
In libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an unprivileged attacker reach this through `wmul(int256 x, int256 y)` while the delegated pool's votes are included in the pool total but not in the denominator, and drive `int256(targetVote) - int256(currentVote)` out of agreement with `the uint256 votes pushed into the Wombat voter` - breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast - for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the delegated pool's votes are included in the pool total but not in the denominator.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the delegated pool's votes are included in the pool total but not in the denominator, snapshot `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter`, run the attacker's `wmul(int256 x, int256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
