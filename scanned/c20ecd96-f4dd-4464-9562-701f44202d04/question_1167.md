# Q1167: ArbitrumMWomAirdrop.claim - claimedAmount written after the external value movement

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the elapsed period count has already exceeded vestingPeriodCount, so that `claimedAmount[account]` diverges from `totalAmount proven by the merkle leaf`, the invariant that the claimed counter must be written before the value it authorises leaves the contract is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the elapsed period count has already exceeded vestingPeriodCount, snapshot `claimedAmount[account]` and `totalAmount proven by the merkle leaf`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
