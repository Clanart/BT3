# Q0558: SignedSafeMath.wdiv - the accumulated net vote is a signed sum of caller-supplied entries

## Question
libraries/SignedSafeMath.sol: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. With the target minus current arithmetic inside WombatBribeManager.castVotes under attacker control and the voter supplies the same pool several times with alternating signs, can an unprivileged caller sequence `wdiv(int256 x, int256 y)` so that `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256` no longer reconcile, violating the invariant that the accumulation of caller-supplied signed values must be bounded at every step and realising Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `totalUserVote accumulated as int256` must stay reconciled with `userTotalVotedInVlmgp as uint256`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the voter supplies the same pool several times with alternating signs, snapshot `totalUserVote accumulated as int256` and `userTotalVotedInVlmgp as uint256`, run the attacker's `wdiv(int256 x, int256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
