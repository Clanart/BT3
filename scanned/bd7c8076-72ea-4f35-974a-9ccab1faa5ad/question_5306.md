# Q5306: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while the bonus reward token registered for the asset is also one of the fee currencies, and drive `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` out of agreement with `_liquidity burned from the receipt token` - breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the bonus reward token registered for the asset is also one of the fee currencies, snapshot `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
