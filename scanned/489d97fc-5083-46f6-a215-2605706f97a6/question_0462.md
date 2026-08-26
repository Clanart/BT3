# Q0462: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
wombat/SimplePoolHelper.sol: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `_amount pulled from the caller` and `the allowance granted to masterMagpie` no longer reconcile, violating the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence atomically under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, asserting at the end that `_amount pulled from the caller` still equals `the allowance granted to masterMagpie` and the PoC's balance delta is non-positive.
