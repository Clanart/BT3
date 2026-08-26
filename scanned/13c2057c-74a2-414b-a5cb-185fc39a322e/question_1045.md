# Q1045: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
rewards/ArbitrumMWomAirdrop.sol: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Under the elapsed period count has already exceeded vestingPeriodCount, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `vested computed in _getClaimable` unreconciled with `claimedAmount[account]`, violates the invariant that a one-time initial release must be recorded as released, not recomputed on every claim, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the elapsed period count has already exceeded vestingPeriodCount, snapshot `vested computed in _getClaimable` and `claimedAmount[account]`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
