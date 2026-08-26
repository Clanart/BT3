# Q1524: ArbitrumMWomAirdrop.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
In rewards/ArbitrumMWomAirdrop.sol, claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Starting from a state where the contract's reward balance is below the sum of unclaimed entitlements, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `claimedAmount[account]` inconsistent with `totalAmount proven by the merkle leaf`, violating the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the contract's reward balance is below the sum of unclaimed entitlements, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the claimed counter must be scoped to the exact leaf that authorised the entitlement.
