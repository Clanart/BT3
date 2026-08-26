# Q2375: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
rewards/ArbitrumMWomAirdrop.sol - claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the claim is placed in the same block as another large claim, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `claimable` and `reward.balanceOf(address(this))` and the invariant that the sum of all claimable amounts must never exceed the tokens actually held, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claim is placed in the same block as another large claim, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `claimable` equals `reward.balanceOf(address(this))` and that no account can withdraw more than it put in.
