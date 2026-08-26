# Q4444: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
Consider wombat/WombatStaking.sol, where _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Assuming the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged attacker turn this into a divergence between `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` via `harvest(address _lpToken)`, breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the deposit token for the pool is wBNB and the helper arrived through depositNative, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
