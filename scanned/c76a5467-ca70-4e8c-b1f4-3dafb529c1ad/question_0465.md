# Q0465: SignedSafeMath.toUint256 - the accumulated net vote is a signed sum of caller-supplied entries

## Question
In libraries/SignedSafeMath.sol, totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Starting from a state where the voter supplies the same pool several times with alternating signs, can an unprivileged EOA use `toUint256(int256 value)` to leave `int256(targetVote) - int256(currentVote)` inconsistent with `the uint256 votes pushed into the Wombat voter`, violating the invariant that the accumulation of caller-supplied signed values must be bounded at every step and extracting Critical - Governance voting result manipulation?

## Target
- File/function: libraries/SignedSafeMath.sol -> `toUint256(int256 value)` (mechanism: the accumulated net vote is a signed sum of caller-supplied entries)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `toUint256(int256 value)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the signed vote delta cast into the unsigned pool counters by WombatBribeManager.vote
- Exploit idea: totalUserVote accumulates every delta as int256 before a single sign test decides whether the unsigned user total is incremented or decremented, so the intermediate accumulation is never bounded. Precondition: the voter supplies the same pool several times with alternating signs.
- Invariant to test: the accumulation of caller-supplied signed values must be bounded at every step; concretely, `int256(targetVote) - int256(currentVote)` must stay reconciled with `the uint256 votes pushed into the Wombat voter`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the voter supplies the same pool several times with alternating signs, have the attacker run `toUint256(int256 value)`, then assert the victim's claimable value and the `int256(targetVote) - int256(currentVote)` versus `the uint256 votes pushed into the Wombat voter` relation are unchanged by the attacker's transaction.
