# Q2075: Airdrop2.claim - initial five percent is added on every evaluation

## Question
In rewards/Airdrop2.sol, vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the claimant sets isLock to false so the plain transfer leg runs, so that `claimedAmount[account]` diverges from `totalAmount proven by the merkle leaf`, the invariant that a one-time initial release must be recorded as released, not recomputed on every claim is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the claimant sets isLock to false so the plain transfer leg runs, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimedAmount[account]` versus `totalAmount proven by the merkle leaf` relation are unchanged by the attacker's transaction.
