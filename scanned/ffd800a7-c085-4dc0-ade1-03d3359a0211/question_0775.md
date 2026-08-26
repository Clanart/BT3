# Q0775: SignedSafeMath.wdiv - signed deltas are cast into unsigned counters

## Question
In libraries/SignedSafeMath.sol, WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Can an unprivileged attacker reach this through `wdiv(int256 x, int256 y)` while targetVote is below currentVote so the second branch of castVotes runs, and drive `int256(targetVote) - int256(currentVote)` out of agreement with `the uint256 votes pushed into the Wombat voter` - breaking the invariant that a signed input must be validated against the unsigned counter it will mutate before the cast - for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: signed deltas are cast into unsigned counters)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: WombatBribeManager.vote casts the voter's int256 delta to uint256 on both branches before adding to or subtracting from unsigned counters, so the sign handling and the counter arithmetic are two separate decisions on caller-supplied values. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: a signed input must be validated against the unsigned counter it will mutate before the cast; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the target minus current arithmetic inside WombatBribeManager.castVotes) under targetVote is below currentVote so the second branch of castVotes runs, asserting on every row that a signed input must be validated against the unsigned counter it will mutate before the cast.
