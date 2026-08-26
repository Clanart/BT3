# Q0958: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
In wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Does `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts after the pull has already happened, so that `IERC20(stakeToken).balanceOf(address(this))` diverges from `the amount credited by IMasterMagpie.depositFor`, the invariant that the account whose tokens fund a deposit must be the account credited with it is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange MasterMagpie is paused so depositFor reverts after the pull has already happened, call `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, and assert `IERC20(stakeToken).balanceOf(address(this))` equals `the amount credited by IMasterMagpie.depositFor` and that no account can withdraw more than it put in.
