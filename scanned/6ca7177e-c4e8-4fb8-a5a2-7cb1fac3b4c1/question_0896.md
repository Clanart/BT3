# Q0896: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
In wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Starting from a state where a residual stakeToken balance from an earlier partial deposit sits on the helper, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that a pass-through that holds value transiently must hold a reentrancy guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence atomically under a residual stakeToken balance from an earlier partial deposit sits on the helper, asserting at the end that `IERC20(stakeToken).balanceOf(address(this))` still equals `the amount credited by IMasterMagpie.depositFor` and the PoC's balance delta is non-positive.
