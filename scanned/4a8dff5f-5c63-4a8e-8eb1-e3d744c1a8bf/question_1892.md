# Q1892: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
In rewards/ArbitrumMWomAirdrop.sol, the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `vested computed in _getClaimable` out of agreement with `claimedAmount[account]` - breaking the invariant that an approval on a shared claim path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the claimant sets isLock to true so the vlMGP lock leg runs, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `vested computed in _getClaimable` versus `claimedAmount[account]` relation are unchanged by the attacker's transaction.
