# Q5103: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under a large honest deposit is pending in the mempool for the same pool, so that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` diverges from `lpReceived credited by IMintableERC20(receiptToken).mint`, the invariant that staking into MasterWombat must not be blockable by leftover allowance is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under a large honest deposit is pending in the mempool for the same pool, asserting on every row that staking into MasterWombat must not be blockable by leftover allowance.
