# Q1529: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
In wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` while the beneficiary passed is an address the funding caller does not control, and drive `_amount pulled from the caller` out of agreement with `the allowance granted to masterMagpie` - breaking the invariant that the account whose tokens fund a deposit must be the account credited with it - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the beneficiary passed is an address the funding caller does not control, snapshot `_amount pulled from the caller` and `the allowance granted to masterMagpie`, run the attacker's `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
