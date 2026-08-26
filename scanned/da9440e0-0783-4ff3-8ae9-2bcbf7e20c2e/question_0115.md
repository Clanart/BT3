# Q0115: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
In rewards/ArbitrumMWomAirdrop.sol, vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the account appears in the merkle tree under two different totalAmount values, so that `vestingPeriodCount and intervals` diverges from `the elapsed period count`, the invariant that a one-time initial release must be recorded as released, not recomputed on every claim is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the account appears in the merkle tree under two different totalAmount values, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `vestingPeriodCount and intervals` versus `the elapsed period count` relation are unchanged by the attacker's transaction.
