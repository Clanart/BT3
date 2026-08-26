# Q0431: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
wombat/SimplePoolHelper.sol: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller` no longer reconcile, violating the invariant that a pass-through that holds value transiently must hold a reentrancy guard and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, snapshot `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller`, run the attacker's `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
