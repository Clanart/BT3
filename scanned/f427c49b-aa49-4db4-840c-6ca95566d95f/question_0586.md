# Q0586: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
In wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, so that `_amount pulled from the caller` diverges from `the allowance granted to masterMagpie`, the invariant that a pass-through that holds value transiently must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, call `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, and assert `_amount pulled from the caller` equals `the allowance granted to masterMagpie` and that no account can withdraw more than it put in.
