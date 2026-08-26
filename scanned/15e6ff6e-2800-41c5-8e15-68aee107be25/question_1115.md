# Q1115: SignedSafeMath.wdiv - the accumulated net vote is a signed sum of caller-supplied entries

## Question
Note that in libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Can an attacker holding only tokens bought on market reach it via `wdiv(int256 x, int256 y)` under targetVote is above currentVote so the first branch of castVotes runs and force `int256(targetVote) - int256(currentVote)` apart from `the uint256 votes pushed into the Wombat voter`, breaking the invariant that the accumulation of caller-supplied signed values must be bounded at every step for Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: targetVote is above currentVote so the first branch of castVotes runs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the target minus current arithmetic inside WombatBribeManager.castVotes) under targetVote is above currentVote so the first branch of castVotes runs, asserting on every row that the accumulation of caller-supplied signed values must be bounded at every step.
