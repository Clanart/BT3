# Q1632: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
In wombat/SimplePoolHelper.sol, authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Starting from a state where the beneficiary passed is an address the funding caller does not control, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`: constrain the setup so that the beneficiary passed is an address the funding caller does not control, fuzz the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake), and assert after every call that a helper must validate the deposit it is asked to make rather than trusting the caller entirely.
