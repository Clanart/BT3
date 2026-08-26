# Q1076: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
rewards/ArbitrumMWomAirdrop.sol: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Under the elapsed period count has already exceeded vestingPeriodCount, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `startVestingTime` unreconciled with `block.timestamp`, violates the invariant that a claim that moves no value must revert rather than mutate state and emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the elapsed period count has already exceeded vestingPeriodCount, asserting at the end that `startVestingTime` still equals `block.timestamp` and the PoC's balance delta is non-positive.
