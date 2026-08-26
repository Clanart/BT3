# Q0617: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
In wombat/SimplePoolHelper.sol, authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, so that `IERC20(stakeToken).balanceOf(address(this))` diverges from `the amount credited by IMasterMagpie.depositFor`, the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, have the attacker run `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, then assert the victim's claimable value and the `IERC20(stakeToken).balanceOf(address(this))` versus `the amount credited by IMasterMagpie.depositFor` relation are unchanged by the attacker's transaction.
