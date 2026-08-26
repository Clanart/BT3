# Q1868: Airdrop2.claim - no check that claimable is non-zero

## Question
rewards/Airdrop2.sol: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Under the claimant sets isLock to true so the vlMGP lock leg runs, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `claimedAmount[account]` unreconciled with `totalAmount proven by the merkle leaf`, violates the invariant that a claim that moves no value must revert rather than mutate state and emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claimant sets isLock to true so the vlMGP lock leg runs, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `claimedAmount[account]` equals `totalAmount proven by the merkle leaf` and that no account can withdraw more than it put in.
