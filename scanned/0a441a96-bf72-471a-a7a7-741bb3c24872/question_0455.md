# Q0455: Airdrop2.claim - no check that claimable is non-zero

## Question
Consider rewards/Airdrop2.sol, where claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a claim that moves no value must revert rather than mutate state and emit and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under block.timestamp is one second before an interval boundary, asserting at the end that `claimedAmount[account]` still equals `totalAmount proven by the merkle leaf` and the PoC's balance delta is non-positive.
