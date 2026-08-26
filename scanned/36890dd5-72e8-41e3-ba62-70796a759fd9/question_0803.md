# Q0803: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
In wombat/SimplePoolHelper.sol, depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Starting from a state where a residual stakeToken balance from an earlier partial deposit sits on the helper, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that the account whose tokens fund a deposit must be the account credited with it and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by WomUp when the caller migrates) under a residual stakeToken balance from an earlier partial deposit sits on the helper, asserting on every row that the account whose tokens fund a deposit must be the account credited with it.
