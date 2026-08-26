# Q0828: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
rewards/ArbitrumMWomAirdrop.sol: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and block.timestamp is one second after an interval boundary, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `vestingPeriodCount and intervals` and `the elapsed period count` no longer reconcile, violating the invariant that the sum of all claimable amounts must never exceed the tokens actually held and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second after an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the sum of all claimable amounts must never exceed the tokens actually held.
