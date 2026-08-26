# Q1339: MGPRelease.claim - claim transfers before recording the claim

## Question
In rewards/MGPRelease.sol, claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Starting from a state where the beneficiary was revoked after having already claimed part of the allocation, can an unprivileged EOA use `claim()` to leave `vested` inconsistent with `beneficiaries[account].totalAlloced - initialUnlockedAmount`, violating the invariant that the claimed counter must be written before the tokens it authorises leave the contract and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: the beneficiary was revoked after having already claimed part of the allocation.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the beneficiary was revoked after having already claimed part of the allocation, call `claim()`, and assert `vested` equals `beneficiaries[account].totalAlloced - initialUnlockedAmount` and that no account can withdraw more than it put in.
