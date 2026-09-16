### Title
Gasless swap `LendAmount` is computed from attacker-controlled nominal gas price rather than the actual (effective) network fee, allowing an unprivileged gasless-tx sender to inflate the KAIA amount fronted by the block proposer - ([File: kaiax/gasless/impl/getter.go])

### Summary
The `kaiax/gasless` module implements KIP‑247 gasless transactions: a block proposer "lends" a user native KAIA to cover the gas of a `GaslessApproveTx`/`GaslessSwapTx` pair, and the user's `swapForGas` call is supposed to repay that amount out of the swap proceeds. The lent/repaid amounts are computed purely from the *nominal* `GasPrice * GasLimit` of the user-submitted transactions (`tx.Fee()`), not from the actual effective gas price that will be charged under Kaia's EIP‑1559-like (Magma) fee mechanism. Because the swap/approve transactions must be legacy-type transactions whose `GasPrice` is fully chosen by the (unprivileged) sender, and no upper bound is enforced on that value in this code path, an attacker can inflate the amount that the proposer's `LendTx` transfers to them, independent of what is actually consumed as gas.

### Finding Description
`decodeFunctionCall` in `kaiax/gasless/impl/getter.go` only recognizes `TxTypeLegacyTransaction` for `GaslessApproveTx`/`GaslessSwapTx`, with the comment "not legacy tx: unable to statically determine the max gas fee" [1](#0-0) . For legacy transactions, `GasPrice`/`Fee()` reflect the nominal price the sender put in the transaction, fully attacker-controlled (bounded only by the tx pool's minimum gas price, not by any maximum).

The lend/repay amounts are derived directly from these attacker-chosen values: [2](#0-1) 

`GetLendTxGenerator` then creates a `LendTx` whose `Value` field is exactly `lendAmount(approveTxOrNil, swapTx)` — i.e., real native KAIA transferred from the proposer's account to the user's account — and whose gas fee cap/tip are copied from the swap tx: [3](#0-2) 

`VerifyExecutable` only checks that `AmountRepay == repayAmount(...)`, i.e., that the repay figure is *internally consistent* with the same attacker-supplied nominal gas price — it never checks that the nominal price bears any relationship to the real/effective fee that will actually be paid by the network for these transactions (which, since Magma, is capped by the block base fee, see `EffectiveGasPrice`/`buyGas` in `blockchain/state_transition.go`): [4](#0-3) [5](#0-4) 

This is structurally the same bug class as TRST-H-1: a value/fee amount owed by one party (there, the wallet owner charged by BrahRouter; here, the block proposer funding `LendTx`) is derived from a gas-price-like field that is fully controlled by an untrusted counterparty (the bot in TRST-H-1; the gasless-tx sender here), with no oracle, cap, or cross-check against the actual cost incurred.

### Impact Explanation
If the nominal `GasPrice` submitted by a gasless-swap user is inflated arbitrarily, the `lendAmount`/`repayAmount` values scale linearly with it, and the `LendTx` moves a correspondingly inflated amount of the proposer's native KAIA to the user's address. Whether this can be fully monetized by the attacker depends on details of the (Solidity) `GaslessSwapRouter.swapForGas` repayment enforcement, which is outside the indexed Go sources and could not be verified here — if repayment is guaranteed atomically and exactly matches the lent amount regardless of size, the direct value-transfer risk is bounded to gas/computation griefing against the proposer (forcing them to front larger-than-necessary temporary balances, and any settlement failure — reverted swap, slippage, deadline miss — after the `LendTx` already executed would leave the proposer under-repaid). This still meets the bar of unauthorized value movement / fee-delegation-style abuse against a specific counterparty (the block proposer) reachable by a single unprivileged transaction sender.

### Likelihood Explanation
The `LendTx`, `ApproveTx`, and `SwapTx` are meant to be bundled together (per `kaiax/gasless/README.md`), which reduces (but, without access to the Solidity contract, cannot be confirmed to eliminate) the risk of a proposer being permanently under-repaid. The attacker only needs to submit an ordinary legacy transaction with an inflated `GasPrice` value that still passes normal tx-pool admission (no explicit maximum-gas-price check was found in this code path), making the precondition trivial to reach for any unprivileged sender. Likelihood is assessed as Medium given the uncertainty around the on-chain repayment guarantee.

### Recommendation
Compute `lendAmount`/`repayAmount` from the actual/effective gas price that will be charged (i.e., mirroring `EffectiveGasPrice`/Magma base-fee logic used in `buyGas`), or enforce an explicit cap on the accepted nominal `GasPrice` for gasless transactions relative to the current network base fee, rather than trusting the raw sender-supplied `GasPrice`/`Fee()` values end-to-end.

### Proof of Concept
Conceptual (Solidity-side enforcement not available in this repo to fully confirm end-to-end exploitability):
1. Attacker crafts a legacy `GaslessApproveTx` and `GaslessSwapTx` with an artificially large `GasPrice` (e.g., far above the current/expected base fee), satisfying `IsApproveTx`/`IsSwapTx` pattern checks in `kaiax/gasless/impl/getter.go`.
2. Attacker sets `AmountRepay` to match `repayAmount(approveTx, swapTx)` computed with the inflated `GasPrice`, satisfying `VerifyExecutable`'s SP4 check [6](#0-5) .
3. The proposer's `LendTx`, generated by `GetLendTxGenerator`, transfers `lendAmount(...)` (inflated) native KAIA to the attacker's address before the swap's real repayment is confirmed [7](#0-6) .
4. Depending on the actual `GaslessSwapRouter.swapForGas` implementation (not present in the indexed Go code), if the swap subsequently reverts, hits slippage, or the repay accounting does not perfectly reconcile against the inflated nominal figure, the proposer is left having transferred more KAIA than it recovers.

Note: full confirmation of exploitability requires inspecting the `GaslessSwapRouter` Solidity contract's repayment logic, which was not available in the indexed codebase content. If the wiki/user needs that contract's source verified, a Devin session with full repository access would be required to close this gap.

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

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
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

**File:** blockchain/state_transition.go (L294-308)
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
```
