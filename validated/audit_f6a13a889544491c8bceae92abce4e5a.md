### Title
Gasless swap admission uses spot-price `GetAmountIn` from live AMM reserves, allowing flashloan-manipulated pool state to bypass mempool checks and grief the fee-lending proposer - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The gasless module's `checkBalanceForSwap` validates a `GaslessSwapTx`'s declared `amountIn` against a value computed by calling `GaslessSwapRouter.GetAmountIn(token, minAmountOut)` on the live chain head state, i.e. directly reading current AMM reserves rather than a time-weighted or otherwise manipulation-resistant price source, analogous to `UniswapHandler._calculateTradeSize` in the referenced Malt report reading pool reserves directly.

### Finding Description
`GaslessModule.checkBalanceForSwap` performs the following check when `BalanceCheckLevelSwapAmount` is enabled (default `BalanceCheckLevelAll`): [1](#0-0) 

`GetAmountIn` is a view call into the `GaslessSwapRouter`/DEX pair contract that derives the required input amount from the pool's current reserves at the moment the transaction is admitted to (or promoted within) the mempool. Because this is a spot-price read of live reserves rather than a TWAP, an attacker can transiently shift the pool reserves (e.g., via a large swap or flash-swap in a preceding transaction/bundle position) to make `GetAmountIn` return an artificially low `requiredAmountIn` for a chosen `minAmountOut`, causing a `GaslessSwapTx` with insufficient `amountIn` to pass admission (`swapArgs.AmountIn.Cmp(requiredAmountIn) < 0` check) that would otherwise be rejected under the true, unmanipulated price. Conversely, an attacker can also manipulate reserves so a legitimately well-formed swap fails admission or, more importantly, is admitted based on stale/manipulated pricing that no longer reflects reality once the manipulating trade is reversed before the block is built.

The gasless flow bundles `[LendTxGenerator, ApproveTx?, SwapTx]`, where the proposer's `LendTxGenerator` (per `GetLendTxGenerator`) unconditionally lends the sender `lendAmount(approveTxOrNil, swapTx)` KAIA up front: [2](#0-1) [3](#0-2) 

The actual repayment enforcement (`swapArgs.AmountRepay.Cmp(repayAmount(...))`) is checked in `VerifyExecutable`, and the real swap execution occurs later on-chain inside `GaslessSwapRouter.swapForGas`, which will use the true reserves at execution time and revert if `minAmountOut` cannot be met: [4](#0-3) 

Because the admission-time `amountIn` check at `tx_pool.go:128-141` is decoupled in time from execution, an admitted `GaslessSwapTx` whose `amountIn` was validated against a manipulated spot price can end up reverting on-chain once the manipulation is reversed (the pool price returns to its real level and the actual `getAmountIn` required is higher than the sender's approved/held `amountIn`). Since the proposer has already lent the gas fee via `LendTxGenerator` before this failure surfaces, and lending is unconditional on the outcome of the swap, a maliciously timed reserve manipulation around gasless-swap admission can cause avoidable on-chain reverts of the bundled `LendTx + SwapTx`, wasting the proposer-funded gas that was fronted to the sender.

### Impact Explanation
This does not directly allow theft of user funds, because `repayAmount`/`lendAmount` are computed independently of swap price (based purely on gas fee accounting), and the ultimate swap execution in `GaslessSwapRouter.swapForGas` re-checks `minAmountOut` against real reserves at execution time, reverting unprofitable swaps. However, it does allow an attacker to cause the admission-time spot-price check to be satisfied with a value that does not hold at execution time, inducing block proposers to include and pay for (via `LendTxGenerator`) bundles that then fail on-chain, wasting proposer-fronted gas and causing confusion/griefing of the gasless subsystem — directly mirroring the Medium severity, "no clear direct profit but real grief" characterization in the referenced Malt report.

### Likelihood Explanation
Exploitation requires the ability to shift AMM reserves for the specific token pair used by `GaslessSwapRouter` at the moment a target `GaslessSwapTx` is being (re-)validated in the mempool (on each `IsReady`/`checkBalanceForSwap` invocation), which is achievable by any unprivileged sender capable of performing large swaps or flash-swaps against the same pool, similar to the original PoC's flashloan step. It requires no privileged access and is reachable purely from public transaction submission, but requires precise timing relative to mempool re-validation and block assembly, limiting reliability.

### Recommendation
Do not rely on a single spot-price view call (`GetAmountIn`) against live reserves for mempool admission decisions. Either (a) use a manipulation-resistant price reference (e.g., TWAP or oracle) for the `ShouldCheckSwapAmount` admission check, or (b) treat the admission-time `GetAmountIn` result only as an advisory/soft check and rely solely on the authoritative on-chain `minAmountOut` enforcement inside `GaslessSwapRouter.swapForGas` at execution time, ensuring `LendTxGenerator`'s lending is not wasted on swaps likely to revert due to transient price manipulation (e.g., re-validate immediately before block inclusion using the same state the block will be built on, and/or bound admission based on a recent multi-block average price).

### Proof of Concept
1. Attacker identifies a `GaslessSwapTx` (their own or targeting the shared pool) is pending admission/promotion and checks `checkBalanceForSwap` via `ShouldCheckSwapAmount()`. [1](#0-0) 
2. Attacker submits a large swap (or flash-swap sequence) against the same pool used by the `GaslessSwapRouter`/token pair to shift reserves such that `GetAmountIn(token, minAmountOut)` returns a lower `requiredAmountIn` than the true post-manipulation price.
3. The crafted `GaslessSwapTx` (with `amountIn` just above the manipulated `requiredAmountIn` but below the true required amount) passes the `swapArgs.AmountIn.Cmp(requiredAmountIn) < 0` check and is admitted/promoted, and the module's `GetLendTxGenerator` builds and signs a `LendTx` funding the sender's gas. [2](#0-1) 
4. Attacker reverses the reserve manipulation (or reserves revert naturally) before the block containing the bundle is finalized/executed; at execution time `GaslessSwapRouter.swapForGas`'s real `minAmountOut` check fails against true reserves, causing the bundled swap to revert on-chain, while the proposer's lent gas fee (`LendTxGenerator`) has already been spent building/broadcasting the bundle.

This report is speculative based on static code reading of the admission-time price check; I was not able to execute or simulate the exact revert/lending interaction end-to-end, so the precise on-chain financial loss magnitude to the proposer is not fully confirmed and should be validated with a live PoC.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L128-141)
```go
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

**File:** kaiax/gasless/impl/getter.go (L268-312)
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
