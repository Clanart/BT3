# Q2145: ArbitrumMWomAirdrop.claim - claimable is not bounded by the contract balance

## Question
Note that in rewards/ArbitrumMWomAirdrop.sol, claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the claimant sets isLock to false so the plain transfer leg runs and force `vestingPeriodCount and intervals` apart from `the elapsed period count`, breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held for Critical - Protocol insolvency?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimable is not bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() transfers or locks the computed claimable with no comparison against reward.balanceOf(address(this)), so once the tree over-allocates relative to the funded balance the remaining claimants simply revert. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the claimant sets isLock to false so the plain transfer leg runs, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vestingPeriodCount and intervals` equals `the elapsed period count` and that no account can withdraw more than it put in.
