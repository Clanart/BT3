# Q0837: SignedSafeMath.wdiv - the accumulated net vote is a signed sum of caller-supplied entries

## Question
In libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Can an unprivileged attacker reach this through `wdiv(int256 x, int256 y)` while targetVote is below currentVote so the second branch of castVotes runs, and drive `int256 delta supplied by the voter` out of agreement with `uint256 pool.totalVoteInVlmgp` - breaking the invariant that the accumulation of caller-supplied signed values must be bounded at every step - for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: targetVote is below currentVote so the second branch of castVotes runs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256 delta supplied by the voter` must stay reconciled with `uint256 pool.totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up targetVote is below currentVote so the second branch of castVotes runs, snapshot `int256 delta supplied by the voter` and `uint256 pool.totalVoteInVlmgp`, run the attacker's `wdiv(int256 x, int256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
