# Q0517: Airdrop2.claim - claimable is not bounded by the contract balance

## Question
Consider rewards/Airdrop2.sol, where claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `startVestingTime` and `block.timestamp` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is one second before an interval boundary, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `startVestingTime` versus `block.timestamp` relation are unchanged by the attacker's transaction.
