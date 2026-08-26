# Q0054: MGPRelease.claim - claim transfers before recording the claim

## Question
Note that in rewards/MGPRelease.sol, claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Can an attacker holding only tokens bought on market reach it via `claim()` under block.timestamp is below startTimestamp and the initial tranche has already been claimed and force `initialUnlockedAmount` apart from `beneficiaries[account].claimed`, breaking the invariant that the claimed counter must be written before the tokens it authorises leave the contract for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim transfers before recording the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() calls IERC20(tokenToRelease).safeTransfer(msg.sender, claimable) and only then executes vesting.claimed += claimable, relying entirely on nonReentrant rather than on check-effects-interactions ordering. Precondition: block.timestamp is below startTimestamp and the initial tranche has already been claimed.
- Invariant to test: the claimed counter must be written before the tokens it authorises leave the contract; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp is below startTimestamp and the initial tranche has already been claimed, have the attacker run `claim()`, then assert the victim's claimable value and the `initialUnlockedAmount` versus `beneficiaries[account].claimed` relation are unchanged by the attacker's transaction.
