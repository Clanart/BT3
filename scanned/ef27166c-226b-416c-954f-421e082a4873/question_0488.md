# Q0488: MGPRelease.claim - claim transfers before recording the claim

## Question
In rewards/MGPRelease.sol, claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `sum of all totalAlloced` out of agreement with `IERC20(tokenToRelease).balanceOf(address(this))` - breaking the invariant that the claimed counter must be written before the tokens it authorises leave the contract - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp is exactly endTimestamp, snapshot `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
