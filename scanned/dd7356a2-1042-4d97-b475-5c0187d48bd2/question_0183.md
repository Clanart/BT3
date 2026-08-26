# Q0183: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
wombat/SimplePoolHelper.sol: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` that leaves `authorized[msg.sender]` unreconciled with `the beneficiary _for chosen by the authorized caller`, violates the invariant that the account whose tokens fund a deposit must be the account credited with it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, snapshot `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller`, run the attacker's `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
