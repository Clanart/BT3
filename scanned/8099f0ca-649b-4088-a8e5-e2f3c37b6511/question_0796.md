# Q0796: Airdrop2.claim - safeApprove without reset on the lock leg

## Question
In rewards/Airdrop2.sol, the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while block.timestamp is one second after an interval boundary, and drive `startVestingTime` out of agreement with `block.timestamp` - breaking the invariant that an approval on a shared claim path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under block.timestamp is one second after an interval boundary, asserting at the end that `startVestingTime` still equals `block.timestamp` and the PoC's balance delta is non-positive.
