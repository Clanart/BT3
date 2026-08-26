# Q1446: ArbitrumMWomAirdrop.claim - claimedAmount written after the external value movement

## Question
rewards/ArbitrumMWomAirdrop.sol - claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the account has already claimed the initial five percent tranche, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `vested computed in _getClaimable` and `claimedAmount[account]` and the invariant that the claimed counter must be written before the value it authorises leaves the contract, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the account has already claimed the initial five percent tranche, snapshot `vested computed in _getClaimable` and `claimedAmount[account]`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
