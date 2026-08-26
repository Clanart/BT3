# Q0493: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
In wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, so that `_amount pulled from the caller` diverges from `the allowance granted to masterMagpie`, the invariant that the account whose tokens fund a deposit must be the account credited with it is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, then assert `_amount pulled from the caller` and `the allowance granted to masterMagpie` end identical in both runs.
