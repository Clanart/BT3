# Q5152: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Under a large honest deposit is pending in the mempool for the same pool, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` unreconciled with `_liquidity burned from the receipt token`, violates the invariant that staking into MasterWombat must not be blockable by leftover allowance, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under a large honest deposit is pending in the mempool for the same pool, asserting on every row that staking into MasterWombat must not be blockable by leftover allowance.
