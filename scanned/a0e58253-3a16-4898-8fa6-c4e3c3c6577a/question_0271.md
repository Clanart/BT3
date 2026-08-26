# Q0271: MGPRelease.claim - claim transfers before recording the claim

## Question
rewards/MGPRelease.sol: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `vested` unreconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`, violates the invariant that the claimed counter must be written before the tokens it authorises leave the contract, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated) under block.timestamp is exactly startTimestamp, asserting on every row that the claimed counter must be written before the tokens it authorises leave the contract.
