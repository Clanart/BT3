# Q0208: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the account appears in the merkle tree under two different totalAmount values, so that `vested computed in _getClaimable` diverges from `claimedAmount[account]`, the invariant that the sum of all claimable amounts must never exceed the tokens actually held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account appears in the merkle tree under two different totalAmount values, then assert `vested computed in _getClaimable` and `claimedAmount[account]` end identical in both runs.
