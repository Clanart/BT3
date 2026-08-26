# Q0217: SignedSafeMath.wdiv - signed deltas are cast into unsigned counters

## Question
Consider libraries/SignedSafeMath.sol, where WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Assuming the voter supplies offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` via `wdiv(int256 x, int256 y)`, breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the target minus current arithmetic inside WombatBribeManager.castVotes) under the voter supplies offsetting positive and negative deltas that net to zero, asserting on every row that a signed input must be validated against the unsigned counter it will mutate before the cast.
