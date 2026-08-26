# Q0276: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
wombat/SimplePoolHelper.sol: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` that leaves `authorized[msg.sender]` unreconciled with `the beneficiary _for chosen by the authorized caller`, violates the invariant that a pass-through that holds value transiently must hold a reentrancy guard, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, have the attacker run `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, then assert the victim's claimable value and the `authorized[msg.sender]` versus `the beneficiary _for chosen by the authorized caller` relation are unchanged by the attacker's transaction.
