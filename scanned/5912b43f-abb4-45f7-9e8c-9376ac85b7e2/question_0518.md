# Q0518: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `startVestingTime` inconsistent with `block.timestamp`, violating the invariant that the sum of all claimable amounts must never exceed the tokens actually held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under block.timestamp is one second before an interval boundary, asserting on every row that the sum of all claimable amounts must never exceed the tokens actually held.
