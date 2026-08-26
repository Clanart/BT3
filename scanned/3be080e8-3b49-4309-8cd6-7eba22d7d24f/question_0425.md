# Q0425: ArbitrumMWomAirdrop.claim - initial five percent is added on every evaluation

## Question
In rewards/ArbitrumMWomAirdrop.sol, vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `claimable` inconsistent with `reward.balanceOf(address(this))`, violating the invariant that a one-time initial release must be recorded as released, not recomputed on every claim and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is one second before an interval boundary, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `claimable` equals `reward.balanceOf(address(this))` and that no account can withdraw more than it put in.
