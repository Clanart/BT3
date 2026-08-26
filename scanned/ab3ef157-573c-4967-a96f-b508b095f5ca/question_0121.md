# Q0121: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
In wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Starting from a state where the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that a pass-through that holds value transiently must hold a reentrancy guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence atomically under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, asserting at the end that `IERC20(stakeToken).balanceOf(address(this))` still equals `the amount credited by IMasterMagpie.depositFor` and the PoC's balance delta is non-positive.
