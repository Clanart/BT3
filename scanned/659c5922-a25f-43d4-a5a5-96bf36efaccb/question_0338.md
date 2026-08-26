# Q0338: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
wombat/SimplePoolHelper.sol: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller` no longer reconcile, violating the invariant that the account whose tokens fund a deposit must be the account credited with it and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`: constrain the setup so that the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, fuzz the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake), and assert after every call that the account whose tokens fund a deposit must be the account credited with it.
