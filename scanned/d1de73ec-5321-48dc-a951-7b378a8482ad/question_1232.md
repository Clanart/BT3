# Q1232: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
wombat/SimplePoolHelper.sol - authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Can an unprivileged attacker controlling _amount and _for, forwarded by WomUp when the caller migrates, under MasterMagpie is paused so depositFor reverts after the pull has already happened, exploit this through `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` to break the reconciliation between `_amount pulled from the caller` and `the allowance granted to masterMagpie` and the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by WomUp when the caller migrates) under MasterMagpie is paused so depositFor reverts after the pull has already happened, asserting on every row that a helper must validate the deposit it is asked to make rather than trusting the caller entirely.
