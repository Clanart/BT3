# Q2329: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
rewards/ArbitrumMWomAirdrop.sol - claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the claim is placed in the same block as another large claim, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `startVestingTime` and `block.timestamp` and the invariant that a claim that moves no value must revert rather than mutate state and emit, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the claim is placed in the same block as another large claim, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that a claim that moves no value must revert rather than mutate state and emit.
