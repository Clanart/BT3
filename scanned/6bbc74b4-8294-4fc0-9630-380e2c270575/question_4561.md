# Q4561: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance and force `_amount minted as mWOM` apart from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, breaking the invariant that an approval on a repeated path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
