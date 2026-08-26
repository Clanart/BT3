# Q1051: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
In wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Does `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts after the pull has already happened, so that `IERC20(stakeToken).balanceOf(address(this))` diverges from `the amount credited by IMasterMagpie.depositFor`, the invariant that a pass-through that holds value transiently must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under MasterMagpie is paused so depositFor reverts after the pull has already happened, asserting on every row that a pass-through that holds value transiently must hold a reentrancy guard.
