# Q1602: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
rewards/ArbitrumMWomAirdrop.sol - vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the contract's reward balance is below the sum of unclaimed entitlements, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `vestingPeriodCount and intervals` and `the elapsed period count` and the invariant that a one-time initial release must be recorded as released, not recomputed on every claim, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the contract's reward balance is below the sum of unclaimed entitlements, asserting on every row that a one-time initial release must be recorded as released, not recomputed on every claim.
