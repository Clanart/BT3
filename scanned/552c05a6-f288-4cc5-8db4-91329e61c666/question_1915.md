# Q1915: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `startVestingTime` out of agreement with `block.timestamp` - breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claimant sets isLock to true so the vlMGP lock leg runs, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `startVestingTime` equals `block.timestamp` and that no account can withdraw more than it put in.
