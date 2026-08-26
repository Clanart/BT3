# Q1879: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` diverges from `_liquidity burned from the receipt token`, the invariant that staking into MasterWombat must not be blockable by leftover allowance is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that staking into MasterWombat must not be blockable by leftover allowance.
