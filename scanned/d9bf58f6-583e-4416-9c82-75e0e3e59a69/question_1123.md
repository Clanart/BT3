# Q1123: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. With _lpToken and the timing of every harvest-driven fee split under attacker control and the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint` no longer reconcile, violating the invariant that staking into MasterWombat must not be blockable by leftover allowance and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM collected as a protocol fee that has not yet been split, call `harvest(address _lpToken)`, and assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` equals `lpReceived credited by IMintableERC20(receiptToken).mint` and that no account can withdraw more than it put in.
