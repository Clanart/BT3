# Q2099: ArbitrumMWomAirdrop.claim - no check that claimable is non-zero

## Question
Note that in rewards/ArbitrumMWomAirdrop.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the claimant sets isLock to false so the plain transfer leg runs and force `vested computed in _getClaimable` apart from `claimedAmount[account]`, breaking the invariant that a claim that moves no value must revert rather than mutate state and emit for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the claimant sets isLock to false so the plain transfer leg runs, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that a claim that moves no value must revert rather than mutate state and emit.
