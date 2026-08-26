# Q1419: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the account has already claimed the initial five percent tranche, and drive `claimedAmount[account]` out of agreement with `totalAmount proven by the merkle leaf` - breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the account has already claimed the initial five percent tranche, snapshot `claimedAmount[account]` and `totalAmount proven by the merkle leaf`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
