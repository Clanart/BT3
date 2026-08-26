# Q0350: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
In wombat/mWOM.sol, the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `deposit(uint256 _amount)` to leave `totalConverted` inconsistent with `totalAccumulated`, violating the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, then assert `totalConverted` and `totalAccumulated` end identical in both runs.
