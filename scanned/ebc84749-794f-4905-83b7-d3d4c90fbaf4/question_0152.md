# Q0152: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
In wombat/SimplePoolHelper.sol, authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Starting from a state where the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `authorized[msg.sender]` inconsistent with `the beneficiary _for chosen by the authorized caller`, violating the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, call `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, and assert `authorized[msg.sender]` equals `the beneficiary _for chosen by the authorized caller` and that no account can withdraw more than it put in.
