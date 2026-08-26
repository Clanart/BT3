# Q1364: Airdrop2.claim - no check that claimable is non-zero

## Question
rewards/Airdrop2.sol: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Under the account has already claimed the initial five percent tranche, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `vestingPeriodCount and intervals` unreconciled with `the elapsed period count`, violates the invariant that a claim that moves no value must revert rather than mutate state and emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the account has already claimed the initial five percent tranche, asserting on every row that a claim that moves no value must revert rather than mutate state and emit.
