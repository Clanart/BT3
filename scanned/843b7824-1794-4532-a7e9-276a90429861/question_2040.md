# Q2040: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `IMintableERC20(poolInfo.receiptToken).totalSupply()` unreconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, violates the invariant that staking into MasterWombat must not be blockable by leftover allowance, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `deposit(address,uint256,uint256,address,address) via a pool helper`: constrain the setup so that a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, fuzz the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper), and assert after every call that staking into MasterWombat must not be blockable by leftover allowance.
