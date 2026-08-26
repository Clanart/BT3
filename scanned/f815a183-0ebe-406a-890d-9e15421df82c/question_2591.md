# Q2591: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
In wombat/WombatStaking.sol, _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Starting from a state where smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged EOA use `harvest(address _lpToken)` to leave `IERC20(poolInfo.lpAddress).balanceOf(address(this))` inconsistent with `lpReceived credited by IMintableERC20(receiptToken).mint`, violating the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, snapshot `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
