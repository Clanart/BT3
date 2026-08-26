# Q2398: ArbitrumMWomAirdrop.claim - claimedAmount written after the external value movement

## Question
rewards/ArbitrumMWomAirdrop.sol - claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the claim is placed in the same block as another large claim, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` and the invariant that the claimed counter must be written before the value it authorises leaves the contract, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the claim is placed in the same block as another large claim, asserting at the end that `claimedAmount[account]` still equals `totalAmount proven by the merkle leaf` and the PoC's balance delta is non-positive.
