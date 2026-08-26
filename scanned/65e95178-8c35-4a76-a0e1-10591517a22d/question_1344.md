# Q1344: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
Consider wombat/SimplePoolHelper.sol, where depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Assuming the stake token has a transfer hook the attacker controls, can an unprivileged attacker turn this into a divergence between `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller` via `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, breaking the invariant that a pass-through that holds value transiently must hold a reentrancy guard and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under the stake token has a transfer hook the attacker controls, asserting on every row that a pass-through that holds value transiently must hold a reentrancy guard.
