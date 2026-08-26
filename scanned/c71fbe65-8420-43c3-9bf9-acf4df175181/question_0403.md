# Q0403: SignedSafeMath.toUint256 - signed deltas are cast into unsigned counters

## Question
In libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Starting from a state where the voter supplies the same pool several times with alternating signs, can an unprivileged EOA use `toUint256(int256 value)` to leave `totalUserVote accumulated as int256` inconsistent with `userTotalVotedInVlmgp as uint256`, violating the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast and extracting Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `toUint256(int256 value)` sequence atomically under the voter supplies the same pool several times with alternating signs, asserting at the end that `totalUserVote accumulated as int256` still equals `userTotalVotedInVlmgp as uint256` and the PoC's balance delta is non-positive.
