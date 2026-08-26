# Q3592: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
Note that in wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an attacker holding only tokens bought on market reach it via `harvest(address _lpToken)` under the pool is marked isPoolFeeFree so the fee loop is skipped entirely and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted in mWOM`, breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` end identical in both runs.
