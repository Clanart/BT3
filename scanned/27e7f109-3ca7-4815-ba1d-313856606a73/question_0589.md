# Q0589: SignedSafeMath.wmul - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol - WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an unprivileged attacker controlling the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at, under targetVote is below currentVote so the second branch of castVotes runs, exploit this through `wmul(int256 x, int256 y)` to break the reconciliation between `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` and the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast, yielding Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `wmul(int256 x, int256 y)`: constrain the setup so that targetVote is below currentVote so the second branch of castVotes runs, fuzz the attacker inputs (the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at), and assert after every call that a signed input must be validated against the unsigned counter it will mutate before the cast.
