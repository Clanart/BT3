# Q0651: SignedSafeMath.wmul - the accumulated net vote is a signed sum of caller-supplied entries

## Question
libraries/SignedSafeMath.sol - totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Can an unprivileged attacker controlling the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at, under targetVote is below currentVote so the second branch of castVotes runs, exploit this through `wmul(int256 x, int256 y)` to break the reconciliation between `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` and the invariant that the accumulation of caller-supplied signed values must be bounded at every step, yielding Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under targetVote is below currentVote so the second branch of castVotes runs, then assert `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` end identical in both runs.
