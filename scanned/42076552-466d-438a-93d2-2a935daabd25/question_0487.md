# Q0487: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
In rewards/ArbitrumMWomAirdrop.sol, the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `vested computed in _getClaimable` inconsistent with `claimedAmount[account]`, violating the invariant that an approval on a shared claim path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second before an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that an approval on a shared claim path must be idempotent.
