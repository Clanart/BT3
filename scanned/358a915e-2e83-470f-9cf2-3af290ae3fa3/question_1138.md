# Q1138: MGPRelease.claim - claim transfers before recording the claim

## Question
Consider rewards/MGPRelease.sol, where claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Assuming initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, can an unprivileged attacker turn this into a divergence between `initialUnlockedAmount` and `beneficiaries[account].claimed` via `claim()`, breaking the invariant that the claimed counter must be written before the tokens it authorises leave the contract and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that the claimed counter must be written before the tokens it authorises leave the contract.
