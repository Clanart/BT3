# Q1607: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
wombat/SimplePoolHelper.sol: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the beneficiary passed is an address the funding caller does not control, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `_amount pulled from the caller` and `the allowance granted to masterMagpie` no longer reconcile, violating the invariant that a pass-through that holds value transiently must hold a reentrancy guard and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence atomically under the beneficiary passed is an address the funding caller does not control, asserting at the end that `_amount pulled from the caller` still equals `the allowance granted to masterMagpie` and the PoC's balance delta is non-positive.
