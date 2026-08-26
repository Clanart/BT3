# Q1628: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
rewards/ArbitrumMWomAirdrop.sol: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Under the contract's reward balance is below the sum of unclaimed entitlements, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `claimable` unreconciled with `reward.balanceOf(address(this))`, violates the invariant that a claim that moves no value must revert rather than mutate state and emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract's reward balance is below the sum of unclaimed entitlements, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimable` versus `reward.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
