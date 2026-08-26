# Q2536: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
Consider rewards/ArbitrumMWomAirdrop.sol, where vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Assuming the computed claimable is exactly zero, can an unprivileged attacker turn this into a divergence between `startVestingTime` and `block.timestamp` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a one-time initial release must be recorded as released, not recomputed on every claim and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the computed claimable is exactly zero.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the computed claimable is exactly zero, asserting on every row that a one-time initial release must be recorded as released, not recomputed on every claim.
