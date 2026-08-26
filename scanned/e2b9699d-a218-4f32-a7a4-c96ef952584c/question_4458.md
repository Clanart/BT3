# Q4458: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
wombat/mWOM.sol: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, is there an unprivileged sequence of `deposit(uint256 _amount)` that leaves `_amount minted as mWOM` unreconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`, violates the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, then assert `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` end identical in both runs.
