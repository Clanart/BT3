# Q5045: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
Consider wombat/mWOM.sol, where the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Assuming the attacker repeats the call across several addresses in the same block, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `deposit(uint256 _amount)`, breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under the attacker repeats the call across several addresses in the same block, asserting at the end that `rewardRatio` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
