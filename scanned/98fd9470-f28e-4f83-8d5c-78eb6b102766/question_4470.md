# Q4470: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
Consider wombat/WombatStaking.sol, where _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Assuming the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `harvest(address _lpToken)`, breaking the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the deposit token for the pool is wBNB and the helper arrived through depositNative, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
