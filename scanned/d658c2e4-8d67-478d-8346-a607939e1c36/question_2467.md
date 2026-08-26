# Q2467: ArbitrumMWomAirdrop.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
Consider rewards/ArbitrumMWomAirdrop.sol, where claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Assuming the computed claimable is exactly zero, can an unprivileged attacker turn this into a divergence between `claimable` and `reward.balanceOf(address(this))` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the computed claimable is exactly zero.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the computed claimable is exactly zero, asserting at the end that `claimable` still equals `reward.balanceOf(address(this))` and the PoC's balance delta is non-positive.
