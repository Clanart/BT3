# Q5499: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. With _lpToken and the timing of every harvest-driven fee split under attacker control and the veWOM contract leaves a non-zero allowance after mint, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that staking into MasterWombat must not be blockable by leftover allowance and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the veWOM contract leaves a non-zero allowance after mint, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `IMintableERC20(poolInfo.receiptToken).totalSupply()` versus `IMasterWombat(masterWombat) staked balance for poolInfo.pid` relation are unchanged by the attacker's transaction.
