# Q1682: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Starting from a state where the beneficiary passed is an address the funding caller does not control, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` to leave `authorized[msg.sender]` inconsistent with `the beneficiary _for chosen by the authorized caller`, violating the invariant that an approval on a shared deposit path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the beneficiary passed is an address the funding caller does not control, snapshot `authorized[msg.sender]` and `the beneficiary _for chosen by the authorized caller`, run the attacker's `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
