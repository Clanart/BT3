# Q1601: Airdrop2.claim - initial five percent is added on every evaluation

## Question
In rewards/Airdrop2.sol, vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Starting from a state where the contract's reward balance is below the sum of unclaimed entitlements, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `vestingPeriodCount and intervals` inconsistent with `the elapsed period count`, violating the invariant that a one-time initial release must be recorded as released, not recomputed on every claim and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract's reward balance is below the sum of unclaimed entitlements, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `vestingPeriodCount and intervals` versus `the elapsed period count` relation are unchanged by the attacker's transaction.
