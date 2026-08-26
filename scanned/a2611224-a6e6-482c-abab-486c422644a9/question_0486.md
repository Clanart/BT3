# Q0486: Airdrop2.claim - safeApprove without reset on the lock leg

## Question
Consider rewards/Airdrop2.sol, where the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `vested computed in _getClaimable` and `claimedAmount[account]` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that an approval on a shared claim path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is one second before an interval boundary, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vested computed in _getClaimable` equals `claimedAmount[account]` and that no account can withdraw more than it put in.
