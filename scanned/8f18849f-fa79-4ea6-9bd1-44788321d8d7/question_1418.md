# Q1418: Airdrop2.claim - claimable is not bounded by the contract balance

## Question
rewards/Airdrop2.sol: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Under the account has already claimed the initial five percent tranche, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `claimedAmount[account]` unreconciled with `totalAmount proven by the merkle leaf`, violates the invariant that the sum of all claimable amounts must never exceed the tokens actually held, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the account has already claimed the initial five percent tranche, asserting on every row that the sum of all claimable amounts must never exceed the tokens actually held.
