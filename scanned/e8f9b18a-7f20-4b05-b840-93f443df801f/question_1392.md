# Q1392: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
rewards/ArbitrumMWomAirdrop.sol - the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the account has already claimed the initial five percent tranche, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `claimable` and `reward.balanceOf(address(this))` and the invariant that an approval on a shared claim path must be idempotent, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the account has already claimed the initial five percent tranche, snapshot `claimable` and `reward.balanceOf(address(this))`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
