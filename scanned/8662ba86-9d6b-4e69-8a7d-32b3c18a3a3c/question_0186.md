# Q0186: SignedSafeMath.toUint256 - the accumulated net vote is a signed sum of caller-supplied entries

## Question
libraries/SignedSafeMath.sol - totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Can an unprivileged attacker controlling the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote, under the voter supplies offsetting positive and negative deltas that net to zero, exploit this through `toUint256(int256 value)` to break the reconciliation between `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` and the invariant that the accumulation of caller-supplied signed values must be bounded at every step, yielding Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the voter supplies offsetting positive and negative deltas that net to zero, have the attacker run `toUint256(int256 value)`, then assert the victim's claimable value and the `int256 delta supplied by the voter` versus `uint256 pool.totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
