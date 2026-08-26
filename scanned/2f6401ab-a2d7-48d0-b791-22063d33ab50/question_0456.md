# Q0456: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `claimedAmount[account]` inconsistent with `totalAmount proven by the merkle leaf`, violating the invariant that a claim that moves no value must revert rather than mutate state and emit and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is one second before an interval boundary, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimedAmount[account]` versus `totalAmount proven by the merkle leaf` relation are unchanged by the attacker's transaction.
