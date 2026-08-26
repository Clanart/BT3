# Q0705: MGPRelease.claim - claim transfers before recording the claim

## Question
rewards/MGPRelease.sol: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `startTimestamp and endTimestamp` and `block.timestamp` no longer reconcile, violating the invariant that the claimed counter must be written before the tokens it authorises leave the contract and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the beneficiary claims repeatedly inside one block, call `claim()`, and assert `startTimestamp and endTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
