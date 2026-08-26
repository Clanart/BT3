# Q0279: SignedSafeMath.wdiv - the accumulated net vote is a signed sum of caller-supplied entries

## Question
Consider libraries/SignedSafeMath.sol, where totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Assuming the voter supplies offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter` via `wdiv(int256 x, int256 y)`, breaking the invariant that the accumulation of caller-supplied signed values must be bounded at every step and producing Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `wdiv(int256 x, int256 y)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(int256 x, int256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target minus current arithmetic inside WombatBribeManager.castVotes
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies offsetting positive and negative deltas that net to zero.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the voter supplies offsetting positive and negative deltas that net to zero, snapshot `int256(targetVote) - int256(currentVote)` and `the uint256 votes pushed into the Wombat voter`, run the attacker's `wdiv(int256 x, int256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
