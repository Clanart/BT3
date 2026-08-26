# Q1260: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
In wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Starting from a state where the stake token has a transfer hook the attacker controls, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `authorized[msg.sender]` inconsistent with `the beneficiary _for chosen by the authorized caller`, violating the invariant that the account whose tokens fund a deposit must be the account credited with it and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the stake token has a transfer hook the attacker controls, then assert `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller` end identical in both runs.
