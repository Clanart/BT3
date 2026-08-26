# Q0858: Airdrop2.claim - claimedAmount written after the external value movement

## Question
In rewards/Airdrop2.sol, claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while block.timestamp is one second after an interval boundary, and drive `claimable` out of agreement with `reward.balanceOf(address(this))` - breaking the invariant that the claimed counter must be written before the value it authorises leaves the contract - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is one second after an interval boundary, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimable` versus `reward.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
