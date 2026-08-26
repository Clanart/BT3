# Q1581: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
Note that in wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` under the beneficiary passed is an address the funding caller does not control and force `authorized[msg.sender]` apart from `the beneficiary _for chosen by the authorized caller`, breaking the invariant that value left on a pass-through helper must be attributable and recoverable for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the beneficiary passed is an address the funding caller does not control, have the attacker run `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, then assert the victim's claimable value and the `authorized[msg.sender]` versus `the beneficiary _for chosen by the authorized caller` relation are unchanged by the attacker's transaction.
