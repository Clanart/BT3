# Q1054: SignedSafeMath.wdiv - signed deltas are cast into unsigned counters

## Question
libraries/SignedSafeMath.sol: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Under targetVote is above currentVote so the first branch of castVotes runs, is there an unprivileged sequence of `wdiv(int256 x, int256 y)` that leaves `totalUserVote accumulated as int256` unreconciled with `userTotalVotedInVlmgp as uint256`, violates the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the target minus current arithmetic inside WombatBribeManager.castVotes) under targetVote is above currentVote so the first branch of castVotes runs, asserting on every row that a signed input must be validated against the unsigned counter it will mutate before the cast.
