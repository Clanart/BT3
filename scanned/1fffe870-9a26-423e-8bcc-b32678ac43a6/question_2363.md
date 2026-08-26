# Q2363: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
In wombat/mWOM.sol, the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under wombatStaking is holding WOM from an earlier deposit that has not been locked, then assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
