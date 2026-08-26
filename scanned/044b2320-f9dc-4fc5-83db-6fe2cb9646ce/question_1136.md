# Q1136: Airdrop2.claim - claimable is not bounded by the contract balance

## Question
In rewards/Airdrop2.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the elapsed period count has already exceeded vestingPeriodCount, so that `claimable` diverges from `reward.balanceOf(address(this))`, the invariant that the sum of all claimable amounts must never exceed the tokens actually held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the elapsed period count has already exceeded vestingPeriodCount, then assert `claimable` and `reward.balanceOf(address(this))` end identical in both runs.
