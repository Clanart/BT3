# Q1398: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
Note that in wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` under the stake token has a transfer hook the attacker controls and force `_amount pulled from the caller` apart from `the allowance granted to masterMagpie`, breaking the invariant that the account whose tokens fund a deposit must be the account credited with it for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the stake token has a transfer hook the attacker controls, call `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, and assert `_amount pulled from the caller` equals `the allowance granted to masterMagpie` and that no account can withdraw more than it put in.
