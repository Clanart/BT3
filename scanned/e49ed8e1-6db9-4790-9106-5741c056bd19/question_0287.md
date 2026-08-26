# Q0287: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
In wombat/WombatStaking.sol, _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `harvest(address _lpToken)` to leave `feeInfos[i].value` inconsistent with `totalFee`, violating the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, then assert `feeInfos[i].value` and `totalFee` end identical in both runs.
