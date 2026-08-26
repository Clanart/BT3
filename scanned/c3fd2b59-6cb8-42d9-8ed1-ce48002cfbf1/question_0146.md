# Q0146: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the account appears in the merkle tree under two different totalAmount values, so that `claimable` diverges from `reward.balanceOf(address(this))`, the invariant that a claim that moves no value must revert rather than mutate state and emit is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the account appears in the merkle tree under two different totalAmount values, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that a claim that moves no value must revert rather than mutate state and emit.
