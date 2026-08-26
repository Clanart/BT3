# Q0930: SignedSafeMath.wmul - the accumulated net vote is a signed sum of caller-supplied entries

## Question
In libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Starting from a state where targetVote is above currentVote so the first branch of castVotes runs, can an unprivileged EOA use `wmul(int256 x, int256 y)` to leave `totalUserVote accumulated as int256` inconsistent with `userTotalVotedInVlmgp as uint256`, violating the invariant that the accumulation of caller-supplied signed values must be bounded at every step and extracting Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under targetVote is above currentVote so the first branch of castVotes runs, then assert `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` end identical in both runs.
