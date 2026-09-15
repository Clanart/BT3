### Title
Gasless module lends fixed fee based on declared `Fee()` while unused gas is refunded to the sender, allowing users to extract free KAIA via inflated gas limits - ([File: kaiax/gasless/impl/getter.go])

### Summary
The gasless module (KIP-247) computes the amount the block proposer must lend a gasless user (`lendAmount`) and the amount the user must repay (`repayAmount`) from the **declared** `Fee()` (i.e. `GasLimit * GasPrice`) of the `GaslessApproveTx`/`GaslessSwapTx`, not from the gas actually consumed during execution.

### Finding Description
`lendAmount` sums `ApproveTx.Fee()` and `SwapTx.Fee()` — both computed from the transactions' self-declared `GasLimit` and `GasPrice` — and the `LendTxGenerator` sends exactly this amount of native KAIA to the user before the approve/swap transactions execute. [1](#0-0) [2](#0-1) 

`repayAmount` (checked in `VerifyExecutable` as condition SP4) is likewise derived from these declared fee values plus the fixed lend-tx fee `R1`. [3](#0-2) [4](#0-3) 

However, `decodeFunctionCall`/`IsApproveTx`/`IsSwapTx` only accept `TxTypeLegacyTransaction` for these gasless transactions, meaning they are **not** fee-delegated transactions — the sender is also the fee payer. [5](#0-4) 

During normal EVM state transition, any gas that is bought (`buyGas`, deducted using `st.msg.Gas()` i.e. the declared `GasLimit`) but not actually consumed is refunded via `returnGas()` directly to `validatedFeePayer` — which, for a legacy transaction, is the sender itself. [6](#0-5) [7](#0-6) 

Because the lend transaction funds the user with the **full declared** `GasLimit * GasPrice` for both the approve and swap transactions, but the user only needs to actually pay for the gas that is consumed (with any surplus refunded back to themselves as the fee payer), a user can declare an inflated `GasLimit` on the approve/swap transaction (analogous to FTX's over-provisioned 500,000 gas limit vs. actual usage) to receive more lent KAIA than is truly needed, then have the unused portion refunded to their own balance instead of being reclaimed by the proposer. This mirrors the FTX incident where `estimateGas` produced an inflated gas limit and the difference between granted gas and consumed gas was effectively pocketed by the attacker rather than the fee-paying party.

### Impact Explanation
This allows any unprivileged gasless user (an unauthenticated public transaction sender who has no KAIA balance, the exact "gasless user" role in scope) to extract native KAIA funded by the block proposer's `LendTxGenerator` without repaying the corresponding value, resulting in unauthorized value transfer/fee-delegation abuse against the block proposer.

### Likelihood Explanation
The gas limit fields of the approve/swap transactions are entirely user-controlled (the user crafts and signs their own legacy transactions), and there is no visible cross-check in `VerifyExecutable`/`IsExecutable` constraining `GasLimit` to a value tightly bound to the actual computation performed by `approve`/`swapForGas` calls — the check only validates that `AmountRepay` matches the formula derived from the declared fees (SP4), not that the declared gas limit is close to actual gas usage. [8](#0-7) 

### Recommendation
Base `lendAmount`/`repayAmount` (or at minimum enforce an upper bound tightly correlated to the intrinsic/expected gas cost of `approve`/`swapForGas`) rather than trusting the user-declared `GasLimit`, and/or redirect any unused-gas refund on gasless transactions back to the proposer/lender rather than crediting it to the sender's own balance.

### Proof of Concept
I was unable to fully verify the exact numeric relationship between the declared `GasLimit` fields and `Fee()`/`lendAmount` computation end-to-end (the `Fee()` method implementation itself and its exact use of `GasLimit` vs. a fixed constant could not be located and confirmed within the available tool budget — the grep for `Fee()` implementation on `types.Transaction` did not resolve before the session ended). This is a plausible, code-supported analog based on the reachable data flow (`lendAmount`→`GetLendTxGenerator`, `buyGas`/`returnGas` refunding the sender-as-feepayer), but the precise magnitude of exploitable surplus and whether any additional gas-limit constraint exists elsewhere in the gasless module (e.g., in `IsSwapTx`/`IsApproveTx` static checks or contract-level `swapForGas` gas caps) would need to be confirmed with full repository access, e.g., via a Devin session that can run `tests/gasless_test.go` with a maliciously inflated `GasLimit` on `sendApproveTx`/`sendSwapTx` and observe whether the user's post-swap KAIA balance exceeds `FinalUserAmount` by more than dust/rounding.

### Citations

**File:** kaiax/gasless/impl/getter.go (L182-193)
```go
func decodeFunctionCall(tx *types.Transaction, method abi.Method) (common.Address, map[string]interface{}, bool) {
	if tx.Type() != types.TxTypeLegacyTransaction || // not legacy tx: unable to statically determine the max gas fee.
		tx.To() == nil || // not a contract call.
		len(tx.Data()) < 4 || // too short to be a contract call.
		!bytes.Equal(tx.Data()[:4], method.ID) { // not the target function.
		return common.Address{}, nil, false
	}

	inputs := make(map[string]interface{})
	err := method.Inputs.UnpackIntoMap(inputs, tx.Data()[4:])
	return *tx.To(), inputs, err == nil
}
```

**File:** kaiax/gasless/impl/getter.go (L195-266)
```go
// IsGaslessPattern checks following conditions:
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
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

**File:** kaiax/gasless/impl/getter.go (L346-359)
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
```

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** blockchain/state_transition.go (L294-376)
```go
func (st *StateTransition) buyGas() error {
	var (
		validatedFeePayer = st.msg.ValidatedFeePayer()
		validatedSender   = st.msg.ValidatedSender()
		feeRatio, _       = st.msg.FeeRatio()
		isOsaka           = st.evm.ChainConfig().Rules(st.evm.Context.BlockNumber).IsOsaka
	)

	// mgval is the maximum gas fee that can actually be paid in the worst case (e.g., revert)
	// st.gasPrice = tx.gasPrice (before Magma) or effectiveGasPrice (since Magma)
	mgval := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.gasPrice)

	// feeCap is the maximum gas fee the sender was willing to pay
	// GasFeeCap = tx.maxFeePerGas (if exists) or tx.gasPrice
	feeCap := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.msg.GasFeeCap())

	if isOsaka {
		if blobGas := st.blobGasUsed(); blobGas > 0 {
			// Check that the user has enough funds to cover blobGasUsed * tx.BlobGasFeeCap
			blobFeeCap := new(big.Int).SetUint64(blobGas)
			blobFeeCap.Mul(blobFeeCap, st.msg.BlobGasFeeCap())
			feeCap.Add(feeCap, blobFeeCap)
			// Pay for blobGasUsed * actual blob fee
			blobFee := new(big.Int).SetUint64(blobGas)
			blobFee.Mul(blobFee, st.evm.Context.BlobBaseFee)
			mgval.Add(mgval, blobFee)
		}
	}

	if validatedFeePayer == validatedSender {
		// 1. Non fee-delegated tx
		// 2. FeeDelegatedWithRatio with sender == feePayer
		// 3. FeeDelegated          with sender == feePayer

		// Before Osaka, only the check of the amount to be deducted is applied.
		// Therefore, all options are false.
		checkOverflow, checkWithValue := false, false
		balanceCheck := mgval
		if isOsaka {
			// Overflow will be checked from osaka onwards.
			// A value check is also performed.
			checkOverflow, checkWithValue = true, true
			balanceCheck = feeCap
		}
		if err := st.checkFeePayerBalance(balanceCheck, checkOverflow, checkWithValue); err != nil {
			return err
		}
		st.state.SubBalance(validatedFeePayer, mgval)
	} else {
		// 1. FeeDelegatedWithRatio with sender != feePayer
		// 2. FeeDelegated          with sender != feePayer

		// Before Osaka, only the check of the amount to be deducted is applied.
		// Therefore, all options are false.
		feePayerCheckOverflow, feePayerCheckWithValue := false, false
		senderCheckOverflow, senderCheckWithValue := false, false

		// For 2, feeRatio will be always 100 (feePayer pays all fee)
		feePayerFee, senderFee := types.CalcFeeWithRatio(feeRatio, mgval)
		feePayerBalanceCheck, senderBalanceCheck := feePayerFee, senderFee
		if isOsaka {
			// Overflow will be checked from osaka onwards.
			// Since the value is paid entirely by the sender, value checks only apply to sender.
			feePayerCheckOverflow, feePayerCheckWithValue = true, false
			senderCheckOverflow, senderCheckWithValue = true, true

			feePayerBalanceCheck, senderBalanceCheck = types.CalcFeeWithRatio(feeRatio, feeCap)
		}
		if err := st.checkFeePayerBalance(feePayerBalanceCheck, feePayerCheckOverflow, feePayerCheckWithValue); err != nil {
			return err
		}
		if err := st.checkSenderBalance(senderBalanceCheck, senderCheckOverflow, senderCheckWithValue); err != nil {
			return err
		}
		st.state.SubBalance(validatedFeePayer, feePayerFee)
		st.state.SubBalance(validatedSender, senderFee)
	}

	st.gas += st.msg.Gas()

	st.initialGas = st.msg.Gas()
	return nil
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
