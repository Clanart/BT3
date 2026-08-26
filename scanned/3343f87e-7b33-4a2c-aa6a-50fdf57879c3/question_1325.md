# Q1325: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol - _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an unprivileged attacker controlling _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `deposit(address,uint256,uint256,address,address) via a pool helper` to break the reconciliation between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` and the invariant that staking into MasterWombat must not be blockable by leftover allowance, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM collected as a protocol fee that has not yet been split, snapshot `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token`, run the attacker's `deposit(address,uint256,uint256,address,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
