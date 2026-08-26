# Q0648: SimplePoolHelper.depositFor - the beneficiary is chosen entirely by the calling contract

## Question
wombat/SimplePoolHelper.sol: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Under a residual stakeToken balance from an earlier partial deposit sits on the helper, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` that leaves `_amount pulled from the caller` unreconciled with `the allowance granted to masterMagpie`, violates the invariant that the account whose tokens fund a deposit must be the account credited with it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: the beneficiary is chosen entirely by the calling contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() pulls stakeToken from msg.sender and credits _for in MasterMagpie, so the authorised caller alone decides who the stake belongs to and the helper performs no attribution of its own. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: the account whose tokens fund a deposit must be the account credited with it; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a residual stakeToken balance from an earlier partial deposit sits on the helper, have the attacker run `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, then assert the victim's claimable value and the `_amount pulled from the caller` versus `the allowance granted to masterMagpie` relation are unchanged by the attacker's transaction.
