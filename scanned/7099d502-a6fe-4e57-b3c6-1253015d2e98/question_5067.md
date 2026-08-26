# Q5067: mWOM.deposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
Consider wombat/mWOM.sol, where for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Assuming the attacker repeats the call across several addresses in the same block, can an unprivileged attacker turn this into a divergence between `totalConverted` and `totalAccumulated` via `deposit(uint256 _amount)`, breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats the call across several addresses in the same block, snapshot `totalConverted` and `totalAccumulated`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
