# Q1653: ArbitrumMWomAirdrop.claim - safeApprove without reset on the lock leg

## Question
rewards/ArbitrumMWomAirdrop.sol: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and the contract's reward balance is below the sum of unclaimed entitlements, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `claimedAmount[account]` and `totalAmount proven by the merkle leaf` no longer reconcile, violating the invariant that an approval on a shared claim path must be idempotent and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: safeApprove without reset on the lock leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock branch calls reward.safeApprove(address(vlmgp), claimable) with no prior zeroing, so allowance residue from a lockFor that under-consumes permanently disables the locking claim path for every claimant. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: an approval on a shared claim path must be idempotent; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract's reward balance is below the sum of unclaimed entitlements, then assert `claimedAmount[account]` and `totalAmount proven by the merkle leaf` end identical in both runs.
