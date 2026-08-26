# Q0555: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
In wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, so that `authorized[msg.sender]` diverges from `the beneficiary _for chosen by the authorized caller`, the invariant that value left on a pass-through helper must be attributable and recoverable is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence atomically under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, asserting at the end that `authorized[msg.sender]` still equals `the beneficiary _for chosen by the authorized caller` and the PoC's balance delta is non-positive.
