# Q0424: Airdrop2.claim - initial five percent is added on every evaluation

## Question
Consider rewards/Airdrop2.sol, where vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `claimable` and `reward.balanceOf(address(this))` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a one-time initial release must be recorded as released, not recomputed on every claim and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: initial five percent is added on every evaluation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested is computed as (totalAmount * 5 / 100) plus the linear term on every call rather than being tracked as a one-time release, so the interaction between the fixed component and the running claimed counter decides whether the first tranche can be taken more than once. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a one-time initial release must be recorded as released, not recomputed on every claim; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp is one second before an interval boundary, snapshot `claimable` and `reward.balanceOf(address(this))`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
