# Q2581: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
rewards/ArbitrumMWomAirdrop.sol: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and the computed claimable is exactly zero, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `claimable` and `reward.balanceOf(address(this))` no longer reconcile, violating the invariant that an approval on a shared claim path must be idempotent and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: the computed claimable is exactly zero.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the computed claimable is exactly zero, asserting at the end that `claimable` still equals `reward.balanceOf(address(this))` and the PoC's balance delta is non-positive.
