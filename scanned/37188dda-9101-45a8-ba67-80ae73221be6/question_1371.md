# Q1371: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
Note that in wombat/SimplePoolHelper.sol, authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` under the stake token has a transfer hook the attacker controls and force `_amount pulled from the caller` apart from `the allowance granted to masterMagpie`, breaking the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under the stake token has a transfer hook the attacker controls, asserting on every row that a helper must validate the deposit it is asked to make rather than trusting the caller entirely.
