# Q1846: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
In rewards/ArbitrumMWomAirdrop.sol, vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `claimable` out of agreement with `reward.balanceOf(address(this))` - breaking the invariant that a one-time initial release must be recorded as released, not recomputed on every claim - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the claimant sets isLock to true so the vlMGP lock leg runs, asserting on every row that a one-time initial release must be recorded as released, not recomputed on every claim.
