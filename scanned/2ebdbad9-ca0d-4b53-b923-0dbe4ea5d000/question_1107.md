# Q1107: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
rewards/ArbitrumMWomAirdrop.sol: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Under the elapsed period count has already exceeded vestingPeriodCount, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `vestingPeriodCount and intervals` unreconciled with `the elapsed period count`, violates the invariant that an approval on a shared claim path must be idempotent, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the elapsed period count has already exceeded vestingPeriodCount, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vestingPeriodCount and intervals` equals `the elapsed period count` and that no account can withdraw more than it put in.
