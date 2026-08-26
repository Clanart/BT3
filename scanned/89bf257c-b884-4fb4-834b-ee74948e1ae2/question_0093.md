# Q0093: SignedSafeMath.wmul - the accumulated net vote is a signed sum of caller-supplied entries

## Question
Note that in libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Can an attacker holding only tokens bought on market reach it via `wmul(int256 x, int256 y)` under the voter supplies offsetting positive and negative deltas that net to zero and force `totalUserVote accumulated as int256` apart from `userTotalVotedInVlmgp as uint256`, breaking the invariant that the accumulation of caller-supplied signed values must be bounded at every step for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the voter supplies offsetting positive and negative deltas that net to zero, then assert `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` end identical in both runs.
