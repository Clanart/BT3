# Q1703: ArbitrumMWomAirdrop.claim - claimedAmount written after the external value movement

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the contract's reward balance is below the sum of unclaimed entitlements, so that `startVestingTime` diverges from `block.timestamp`, the invariant that the claimed counter must be written before the value it authorises leaves the contract is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the contract's reward balance is below the sum of unclaimed entitlements, asserting on every row that the claimed counter must be written before the value it authorises leaves the contract.
