# Q0307: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
wombat/SimplePoolHelper.sol: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` that leaves `_amount pulled from the caller` unreconciled with `the allowance granted to masterMagpie`, violates the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`: constrain the setup so that the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, fuzz the attacker inputs (_amount and _for, forwarded by WomUp when the caller migrates), and assert after every call that a helper must validate the deposit it is asked to make rather than trusting the caller entirely.
