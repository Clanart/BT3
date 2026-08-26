# Q0827: Airdrop2.claim - claimable is not bounded by the contract balance

## Question
In rewards/Airdrop2.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while block.timestamp is one second after an interval boundary, and drive `vestingPeriodCount and intervals` out of agreement with `the elapsed period count` - breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is one second after an interval boundary, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vestingPeriodCount and intervals` equals `the elapsed period count` and that no account can withdraw more than it put in.
