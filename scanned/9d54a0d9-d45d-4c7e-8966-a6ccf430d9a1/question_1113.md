# Q1113: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
wombat/SimplePoolHelper.sol: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. With _amount and _for, forwarded by WomUp when the caller migrates under attacker control and MasterMagpie is paused so depositFor reverts after the pull has already happened, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` so that `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller` no longer reconcile, violating the invariant that the account whose tokens fund a deposit must be the account credited with it and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`: constrain the setup so that MasterMagpie is paused so depositFor reverts after the pull has already happened, fuzz the attacker inputs (_amount and _for, forwarded by WomUp when the caller migrates), and assert after every call that the account whose tokens fund a deposit must be the account credited with it.
