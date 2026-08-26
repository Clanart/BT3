# Q0772: SimplePoolHelper.depositFor - the authorised caller set can only grow through the owner

## Question
wombat/SimplePoolHelper.sol: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Under a residual stakeToken balance from an earlier partial deposit sits on the helper, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` that leaves `IERC20(stakeToken).balanceOf(address(this))` unreconciled with `the amount credited by IMasterMagpie.depositFor`, violates the invariant that a helper must validate the deposit it is asked to make rather than trusting the caller entirely, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the authorised caller set can only grow through the owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: authorize and unauthorize are owner-only while depositFor trusts every authorised address completely, so the blast radius of any bug in an authorised contract is the whole helper. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: a helper must validate the deposit it is asked to make rather than trusting the caller entirely; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual stakeToken balance from an earlier partial deposit sits on the helper, snapshot `IERC20(stakeToken).balanceOf(address(this))` and `the amount credited by IMasterMagpie.depositFor`, run the attacker's `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
