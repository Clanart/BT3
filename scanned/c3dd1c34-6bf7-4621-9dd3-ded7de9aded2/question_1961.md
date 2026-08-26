# Q1961: ArbitrumMWomAirdrop.claim - the destination is chosen by the claimant

## Question
In rewards/ArbitrumMWomAirdrop.sol, the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `claimable` out of agreement with `reward.balanceOf(address(this))` - breaking the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the claimant sets isLock to true so the vlMGP lock leg runs, snapshot `claimable` and `reward.balanceOf(address(this))`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
