Found the strongest analog: `kaiax/gasless`'s `repayAmount`/`lendAmount` calculation, which computes the fixed repayment amount from `swapTx.GasPrice() * TxGas` plus each transaction's declared `Fee()`, but this repayment is fixed at tx-construction time while the actual gas consumed by execution (e.g., ApproveTx/SwapTx VM execution, refunds, floor data gas under EIP-7623) can diverge from the declared gas limits used in `lendAmount`/`repayAmount`. This mirrors the Arrakis bug class: an amount (liquidity to mint) computed from a snapshot that doesn't reconcile with the actual settlement (liquidity burned), causing a net loss.### Title
Gasless repay amount is computed from declared gas limits, not actual execution cost, allowing proposer under-repayment / over-lending mismatch - (File: kaiax/gasless/impl/getter.go)

### Summary
The Arrakis report describes a class of bug where an amount to be settled (liquidity to mint) is calculated from a value snapshot that is not reconciled against the actual amount being withdrawn/burned, so the two legs of the operation can diverge and cause a net loss. The same "compute-then-settle-without-reconciliation" pattern exists in Kaia's `kaiax/gasless` module (KIP-247), where the block proposer lends KAIA to a gasless sender and the sender's repayment amount is computed purely from the *declared* gas price/gas limit of the bundled transactions at bundling time, not from the gas actually consumed at execution time.

### Finding Description
`lendAmount()` and `repayAmount()` in [1](#0-0)  compute the amount the proposer lends to the gasless sender, and the amount the sender must repay, using `tx.Fee()` (i.e., `GasPrice * GasLimit`, the declared/approved values) of the `ApproveTx`/`SwapTx`, plus `swapTx.GasPrice() * params.TxGas` for the `LendTx` itself:

```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}
	r.Add(r, swapTx.Fee())
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

This `repayAmount` is verified statically against `SwapArgs.AmountRepay` in `VerifyExecutable` (`SP4` check) at admission time [2](#0-1) , and the `LendTx` value is fixed to `lendAmount()` when the bundle is generated [3](#0-2) .

However, `GasPrice * GasLimit` is the *maximum authorized* fee, not the fee that is actually deducted from the sender by `TransitionDb()`. Actual fee deduction is based on `gasUsed`, which can differ from `GasLimit` due to gas refunds (`calcRefund`), EIP-7623 floor-data-gas adjustments, or execution taking less gas than the declared limit [4](#0-3) [5](#0-4) . The codebase itself acknowledges this exact mismatch class elsewhere (`AccountMap.AdjustFeesByReceipts`), which exists specifically because "`Update()` uses `IntrinsicGas` as an approximation ... causing a mismatch" between declared/estimated fees and actual receipts-based fees [6](#0-5) . That reconciliation mechanism exists for the general fee-accounting path but there is no equivalent reconciliation for the gasless `lendAmount`/`repayAmount` pair: the proposer lends exactly `Fee() = GasPrice * GasLimit` and the sender's fixed, pre-declared `AmountRepay` is checked to equal that same static formula — there is no adjustment step analogous to `AdjustFeesByReceipts` for the gasless bundle.

### Impact Explanation
Because `lendAmount`/`repayAmount` are derived purely from declared gas limits and never reconciled against actual `gasUsed` from the corresponding receipts, an attacker who is the gasless sender can maximize the discrepancy between "authorized" and "actually consumed" gas (e.g., by setting `ApproveTx`/`SwapTx` gas limits far above what execution will realistically consume while still passing static verification, since `VerifyExecutable` only checks that `AmountRepay == repayAmount(...)` derived from the same declared limits, not against real gas usage). The proposer's `LendTx` unconditionally transfers `lendAmount()` (based on `GasLimit`) to the sender, while the actual fee burned/collected from the sender's `ApproveTx`/`SwapTx` during `TransitionDb` is based on `gasUsed`, which is refunded/returned via `returnGas()` to the *fee payer* (the sender) for unused gas — not back to the proposer who fronted the value transfer via `LendTx`. This creates a value-extraction path where the sender receives lent KAIA calibrated to gas *limits* it never actually spends, and, depending on how the repayment/settlement flow nets out, the proposer can be systematically under-repaid relative to what it actually lent, a fee-delegation abuse directly analogous to the Arrakis pattern where minted/burned amounts are computed from a value that doesn't match what is truly consumed/returned.

### Likelihood Explanation
This is reachable by any unprivileged gasless-transaction sender who can freely choose the `GasLimit` of the `ApproveTx`/`SwapTx` (subject to admission checks in `checkBalanceForSwap`/`checkBalanceForApprove`, which validate balances/allowances/amountIn but do not bound the relationship between declared gas limit and expected real execution cost) [7](#0-6) . No special privileges, validator collusion, or network-level manipulation are required — a single crafted gasless transaction pair submitted through the public RPC/tx pool is sufficient to trigger the mismatch.

### Recommendation
Add a reconciliation step for the gasless `LendTx`/repayment flow analogous to `AccountMap.AdjustFeesByReceipts`: after block execution, compare the actual `gasUsed` (from receipts) of the `ApproveTx`/`SwapTx` against the `GasLimit`-based `lendAmount`/`repayAmount` used to size the `LendTx`, and either (a) bound `lendAmount`/`repayAmount` to the minimum of declared limit and a conservative estimate of real gas usage, or (b) settle the difference back to the proposer/rewardbase at the end of block processing, mirroring the existing `AdjustFeesByReceipts` mechanism.

### Proof of Concept
1. Submit a `GaslessApproveTx` and `GaslessSwapTx` pair with `GasLimit` values set well above the realistic gas needed for the approve/swap calls (but still within block gas limits and passing `checkBalanceForApprove`/`checkBalanceForSwap` static checks).
2. `VerifyExecutable`'s `SP4` check accepts the pair because `AmountRepay` is derived from the same declared `GasPrice * GasLimit` formula in `repayAmount()` [2](#0-1) .
3. `GetLendTxGenerator` creates a `LendTx` transferring `lendAmount()` (based on `GasLimit`) from the proposer to the sender [3](#0-2) .
4. During execution, `TransitionDb` charges the sender only for actual `gasUsed` (which is less than `GasLimit`) and refunds unused gas back to the sender via `returnGas()` [8](#0-7) , so the sender's net repayment obligation (fixed at `AmountRepay`) no longer matches what the proposer actually needed to lend for the real gas cost, producing a proposer-side value mismatch each time the gasless flow is used with inflated gas limits.

### Citations

**File:** kaiax/gasless/impl/getter.go (L260-263)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}
```

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
}
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** blockchain/state_transition.go (L633-658)
```go
	}

	// Compute refund
	gasRefund := st.calcRefund()
	st.gas += gasRefund
	if rules.IsPrague {
		// After EIP-7623: Data-heavy transactions pay the floor gas.
		if st.gasUsed() < floorDataGas {
			st.gas = st.initialGas - floorDataGas
		}
	}
	st.returnGas()

	// Defer transferring Tx fee when DeferredTxFee is true
	// DeferredTxFee has never been voted, so it's ok to use the genesis value instead of the latest value from governance.
	if st.evm.ChainConfig().Governance == nil || !st.evm.ChainConfig().Governance.DeferredTxFee() {
		if rules.IsMagma {
			// Since Magma, half the fee is burned and the other half goes to the proposer (Rewardbase).
			fee := new(big.Int).Mul(new(big.Int).SetUint64(st.gasUsed()), st.gasPrice)
			burnt := new(big.Int).Div(fee, big.NewInt(2)) // fee / 2
			distributed := new(big.Int).Sub(fee, burnt)
			st.state.AddBalance(st.evm.Context.Rewardbase, distributed)
		} else {
			st.state.AddBalance(st.evm.Context.Coinbase, new(big.Int).Mul(new(big.Int).SetUint64(st.gasUsed()), st.gasPrice))
		}
	}
```

**File:** blockchain/state_transition.go (L796-810)
```go
// calcRefund computes refund counter, capped to a refund quotient.
func (st *StateTransition) calcRefund() uint64 {
	var gasRefund uint64
	if !st.evm.ChainConfig().Rules(st.evm.Context.BlockNumber).IsKore {
		// Before EIP-3529: refunds were capped to gasUsed / 2
		gasRefund = st.gasUsed() / params.RefundQuotient
	} else {
		// After EIP-3529: refunds are capped to gasUsed / 5
		gasRefund = st.gasUsed() / params.RefundQuotientEIP3529
	}
	if gasRefund > st.state.GetRefund() {
		gasRefund = st.state.GetRefund()
	}
	return gasRefund
}
```

**File:** blockchain/state_transition.go (L812-828)
```go
// returnGas returns KAIA for remaining gas, exchanged at the original rate.
func (st *StateTransition) returnGas() {
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(st.gas), st.gasPrice)

	validatedFeePayer := st.msg.ValidatedFeePayer()
	validatedSender := st.msg.ValidatedSender()
	feeRatio, isRatioTx := st.msg.FeeRatio()
	if isRatioTx {
		feePayer, feeSender := types.CalcFeeWithRatio(feeRatio, remaining)

		st.state.AddBalance(validatedFeePayer, feePayer)
		st.state.AddBalance(validatedSender, feeSender)
	} else {
		// To make a short circuit, the below routine processes when feeRatio == 100.
		st.state.AddBalance(validatedFeePayer, remaining)
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L62-182)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}

func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
}

// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```
