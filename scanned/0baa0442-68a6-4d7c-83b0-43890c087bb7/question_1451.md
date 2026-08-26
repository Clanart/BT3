# Q1451: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
In wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the stake token has a transfer hook the attacker controls, so that `authorized[msg.sender]` diverges from `the beneficiary _for chosen by the authorized caller`, the invariant that value left on a pass-through helper must be attributable and recoverable is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the stake token has a transfer hook the attacker controls, snapshot `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller`, run the attacker's `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
