# Q0741: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
wombat/SimplePoolHelper.sol: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Under a residual stakeToken balance from an earlier partial deposit sits on the helper, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` that leaves `_amount pulled from the caller` unreconciled with `the allowance granted to masterMagpie`, violates the invariant that a pass-through that holds value transiently must hold a reentrancy guard, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a residual stakeToken balance from an earlier partial deposit sits on the helper, then assert `_amount pulled from the caller` and `the allowance granted to masterMagpie` end identical in both runs.
