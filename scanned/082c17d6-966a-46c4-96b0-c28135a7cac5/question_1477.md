# Q1477: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
In wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` while the stake token has a transfer hook the attacker controls, and drive `_amount pulled from the caller` out of agreement with `the allowance granted to masterMagpie` - breaking the invariant that a pass-through that holds value transiently must hold a reentrancy guard - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by WomUp when the caller migrates) under the stake token has a transfer hook the attacker controls, asserting on every row that a pass-through that holds value transiently must hold a reentrancy guard.
