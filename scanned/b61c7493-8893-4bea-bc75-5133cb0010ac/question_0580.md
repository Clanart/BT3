# Q0580: ArbitrumMWomAirdrop.claim - the destination is chosen by the claimant

## Question
In rewards/ArbitrumMWomAirdrop.sol, the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `claimable` inconsistent with `reward.balanceOf(address(this))`, violating the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp is one second before an interval boundary, snapshot `claimable` and `reward.balanceOf(address(this))`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
