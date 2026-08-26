# Q4765: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
Consider wombat/mWOM.sol, where the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Assuming the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged attacker turn this into a divergence between `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` via `deposit(uint256 _amount)`, breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, then assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
