# Q0735: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
rewards/ArbitrumMWomAirdrop.sol: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and block.timestamp is one second after an interval boundary, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `claimedAmount[account]` and `totalAmount proven by the merkle leaf` no longer reconcile, violating the invariant that a one-time initial release must be recorded as released, not recomputed on every claim and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under block.timestamp is one second after an interval boundary, asserting at the end that `claimedAmount[account]` still equals `totalAmount proven by the merkle leaf` and the PoC's balance delta is non-positive.
