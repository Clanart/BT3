# Q0744: SignedSafeMath.toUint256 - the accumulated net vote is a signed sum of caller-supplied entries

## Question
Consider libraries/SignedSafeMath.sol, where totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Assuming targetVote is below currentVote so the second branch of castVotes runs, can an unprivileged attacker turn this into a divergence between `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` via `toUint256(int256 value)`, breaking the invariant that the accumulation of caller-supplied signed values must be bounded at every step and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish targetVote is below currentVote so the second branch of castVotes runs, have the attacker run `toUint256(int256 value)`, then assert the victim's claimable value and the `totalUserVote accumulated as int256` versus `userTotalVotedInVlmgp as uint256` relation are unchanged by the attacker's transaction.
