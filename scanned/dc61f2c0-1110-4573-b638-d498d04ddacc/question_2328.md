# Q2328: Airdrop2.claim - no check that claimable is non-zero

## Question
In rewards/Airdrop2.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Starting from a state where the claim is placed in the same block as another large claim, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `startVestingTime` inconsistent with `block.timestamp`, violating the invariant that a claim that moves no value must revert rather than mutate state and emit and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claim is placed in the same block as another large claim, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `startVestingTime` equals `block.timestamp` and that no account can withdraw more than it put in.
