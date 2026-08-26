# Q4800: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
Note that in wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an attacker holding only tokens bought on market reach it via `harvest(address _lpToken)` under the attacker deposits and withdraws through the same helper inside one transaction and force `isPoolFeeFree[_lpToken]` apart from `feeInfos.length`, breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the same helper inside one transaction, then assert `isPoolFeeFree[_lpToken]` and `feeInfos.length` end identical in both runs.
