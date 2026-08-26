# Q0922: MGPRelease.claim - claim transfers before recording the claim

## Question
Consider rewards/MGPRelease.sol, where claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `beneficiaries[account].claimed` and `getClaimable(account)` via `claim()`, breaking the invariant that the claimed counter must be written before the tokens it authorises leave the contract and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that the contract balance is below the sum of unclaimed allocations, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that the claimed counter must be written before the tokens it authorises leave the contract.
