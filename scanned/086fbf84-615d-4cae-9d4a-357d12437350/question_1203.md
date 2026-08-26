# Q1203: SimplePoolHelper.depositFor - no reentrancy guard on the pass-through

## Question
Note that in wombat/SimplePoolHelper.sol, depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` under MasterMagpie is paused so depositFor reverts after the pull has already happened and force `authorized[msg.sender]` apart from `the beneficiary _for chosen by the authorized caller`, breaking the invariant that a pass-through that holds value transiently must hold a reentrancy guard for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: no reentrancy guard on the pass-through)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() performs safeTransferFrom, safeApprove and an external MasterMagpie deposit with no nonReentrant, so a stake token with a transfer hook re-enters between the pull and the credit. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: a pass-through that holds value transiently must hold a reentrancy guard; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence atomically under MasterMagpie is paused so depositFor reverts after the pull has already happened, asserting at the end that `authorized[msg.sender]` still equals `the beneficiary _for chosen by the authorized caller` and the PoC's balance delta is non-positive.
