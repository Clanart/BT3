# Q1523: Airdrop2.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
Consider rewards/Airdrop2.sol, where claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Assuming the contract's reward balance is below the sum of unclaimed entitlements, can an unprivileged attacker turn this into a divergence between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's reward balance is below the sum of unclaimed entitlements, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `claimedAmount[account]` equals `totalAmount proven by the merkle leaf` and that no account can withdraw more than it put in.
