# Q2098: Airdrop2.claim - no check that claimable is non-zero

## Question
In rewards/Airdrop2.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the claimant sets isLock to false so the plain transfer leg runs, so that `vested computed in _getClaimable` diverges from `claimedAmount[account]`, the invariant that a claim that moves no value must revert rather than mutate state and emit is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claimant sets isLock to false so the plain transfer leg runs, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vested computed in _getClaimable` equals `claimedAmount[account]` and that no account can withdraw more than it put in.
