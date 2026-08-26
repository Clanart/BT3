# Q1678: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Starting from a state where the contract's reward balance is below the sum of unclaimed entitlements, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `vested computed in _getClaimable` inconsistent with `claimedAmount[account]`, violating the invariant that the sum of all claimable amounts must never exceed the tokens actually held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's reward balance is below the sum of unclaimed entitlements, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vested computed in _getClaimable` equals `claimedAmount[account]` and that no account can withdraw more than it put in.
