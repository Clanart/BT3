# Q4047: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `feeInfos[i].value` diverges from `totalFee`, the invariant that staking into MasterWombat must not be blockable by leftover allowance is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under several feeInfos entries are active at once and the harvested amount is small, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.
