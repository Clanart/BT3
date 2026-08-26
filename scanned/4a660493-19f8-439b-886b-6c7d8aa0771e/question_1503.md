# Q1503: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
In wombat/SimplePoolHelper.sol, authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Starting from a state where the stake token has a transfer hook the attacker controls, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the stake token has a transfer hook the attacker controls, have the attacker run `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, then assert the victim's claimable value and the `IERC20(stakeToken).balanceOf(address(this))` versus `the amount credited by IMasterMagpie.depositFor` relation are unchanged by the attacker's transaction.
