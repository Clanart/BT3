# Q0372: SignedSafeMath.wmul - the accumulated net vote is a signed sum of caller-supplied entries

## Question
In libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Does `wmul(int256 x, int256 y)` let an unprivileged caller exploit that under the voter supplies the same pool several times with alternating signs, so that `int256 delta supplied by the voter` diverges from `uint256 pool.totalVoteInVlmgp`, the invariant that the accumulation of caller-supplied signed values must be bounded at every step is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wmul(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed deltas in the WombatBribeManager.vote array and the operand scale they are applied at
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the voter supplies the same pool several times with alternating signs, then assert `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp` end identical in both runs.
