### Title
Block gas-limit check trusts a self-reported `GasEstimate` instead of actual EVM execution gas, allowing heavy blocks to bypass the DoS guard - (File: app/app.go)

### Summary
`ProcessProposalHandler` calls `checkTotalBlockGas` to reject proposals whose aggregate transaction gas would exceed the consensus `MaxGas`/`MaxGasWanted` block limits, which is exactly the kind of "benchmark the cost per block and enforce a limit" mitigation the external report recommends for the `finalizeBlocks`-style heavy-block problem. However, for EVM transactions the function sums the **tx-supplied `GasEstimate`** rather than the actual gas the transaction is entitled to consume (`etx.Gas()`), as long as `GasEstimate` is `>= MinGasEVMTx (21000)` and `<= gasWanted`. Since the real EVM state-transition can legitimately consume up to `gasWanted`, an attacker can submit transactions with `gasWanted` close to `MaxGas` while their `GasEstimate` field is set to the minimum (21000), causing `checkTotalBlockGas` to massively under-count the true cost of the block. [1](#0-0) 

### Finding Description
`checkTotalBlockGas` is the guard that stands between `ProcessProposal` and block execution: [2](#0-1) 

For EVM transactions, `gasWanted` is the real gas limit (`etx.Gas()`) that will be metered during execution, but the accumulator used against `ctx.ConsensusParams().Block.MaxGas` (`totalGas`) is populated from `decodedTx.GetGasEstimate()` whenever that estimate is `>= MinGasEVMTx` and `<= gasWanted`: [3](#0-2) 

`GetGasEstimate()` is part of the generic `sdk.Tx` interface and is a value carried by the transaction/tx-builder itself (see `sei-cosmos/types/tx_msg.go`), not something re-derived by simulating the transaction during `ProcessProposal`: [4](#0-3) 

The corresponding EVM ante decorator similarly just forwards whatever `tx.GetGasEstimate()` reports into the context, with no cross-check against actual metered execution: [5](#0-4) 

Because `checkTotalBlockGas` only rejects a proposal when `totalGas > maxGas` (using the possibly deflated `GasEstimate` sum) or `totalGasWanted > maxGasWanted` (a separate, typically much larger cap), a block can pass this guard while containing transactions whose real EVM execution (via `ExecuteTxsConcurrently` → `ProcessBlock`) consumes gas far beyond `MaxGas`. This reproduces the exact bug class from the external report: the finalization pipeline (`ProcessBlock` → `FinalizeBlocker`, `app/app.go:1764` and `app/app.go:1272`) can be handed a block whose true gas requirement exceeds the intended block gas budget, because the pre-check benchmarked a self-reported estimate rather than the enforced execution ceiling. [6](#0-5) 

### Impact Explanation
If the real per-block EVM gas usage is allowed to exceed the configured `MaxGas`, block execution time in `ProcessBlock`/`FinalizeBlocker` (transaction execution, deferred EndBlock aggregation over all EVM transactions, receipt writing) grows well past what the network sized for, which is the direct "block finalization issues" / "delayed block" risk called out in the report. This can push block production past the 2.5s threshold or, in the worst case, repeatedly force nodes into abnormally heavy blocks that stall consensus progress.

### Likelihood Explanation
Any unprivileged EVM transaction sender controls both `gasWanted` (`etx.Gas()`) and, indirectly through how their transaction is encoded/decoded, the `GasEstimate` value read by `checkTotalBlockGas`. No special privileges (validator, governance, etc.) are required to submit such transactions; they only need to be included by any proposer, which happens as part of normal mempool inclusion. I was not able to fully trace, within the available tool budget, the exact code path that populates the `GasEstimate` field for a transaction originating from a plain `eth_sendRawTransaction` (i.e., whether it is always locally simulated by the receiving node before being embedded in the Cosmos tx envelope, which would reduce attacker control, or whether it is taken verbatim from client-supplied data). This is a meaningful gap: if `GasEstimate` is always computed locally via simulation by each node from the raw tx bytes (deterministically, so all validators agree), the attack surface narrows to cases where the local gas estimator itself is imprecise or gameable (e.g., data-dependent execution paths, storage warm/cold effects) rather than a directly forgeable field.

### Recommendation
- Do not let `checkTotalBlockGas` trust a value that can diverge from the enforced execution ceiling; account EVM transactions by `gasWanted` (`etx.Gas()`) directly for the `MaxGas` check, or clamp `GasEstimate` usage so it can never let the accepted total exceed `MaxGas` even in the worst case (i.e., always use `max(estimate, gasWanted)` for the `MaxGas` comparison, not `min`).
- If `GasEstimate` is intended purely as a mempool prioritization/throughput heuristic, keep it out of the hard `MaxGas` DoS-prevention path entirely and gate `ProcessProposal` acceptance solely on `gasWanted`.
- Add regression tests mirroring `TestCheckTotalBlockGas_GasEstimatePreferredOverGasWanted` but asserting the proposal is *rejected* when the sum of real `gasWanted` values would blow past `MaxGas`, even though supplied `GasEstimate`s are low.

### Proof of Concept
1. Craft `N` signed EVM transactions where each transaction sets `gasWanted` (`etx.Gas()`) close to `MaxGas` (e.g., a heavy contract call/loop), but the transaction's `GasEstimate` field is populated with the minimum accepted value, `MinGasEVMTx = 21000` (see `app/app.go:2663`, `x/evm/ante/gas.go:13`).
2. Submit all `N` transactions to the mempool; a proposer includes them in a single block.
3. In `ProcessProposalHandler` → `checkTotalBlockGas`, `totalGas` accumulates only `21000 * N` (since `est <= gasWanted` and `est >= MinGasEVMTx`), which stays under `MaxGas`, while `totalGasWanted` accumulates `N * gasWanted`, checked only against the separate, larger `MaxGasWanted` cap — so the proposal is accepted (`app/app.go:2653-2674`).
4. During actual execution in `ProcessBlock` (`app/app.go:1764-1836`), each transaction's real EVM execution can consume up to its full `gasWanted`, so the block's true aggregate gas usage vastly exceeds `MaxGas`, inflating `ProcessBlock`/`FinalizeBlocker` execution time beyond the budget the `MaxGas` limit was meant to enforce.

### Citations

**File:** app/app.go (L1806-1836)
```go
	// Execute all transactions
	txResults, ctx = app.ExecuteTxsConcurrently(ctx, txs, typedTxs)

	midBlockEvents := app.MidBlock(ctx, req.Height)
	events = append(events, midBlockEvents...)

	// Flush giga stores so WriteDeferredBalances (which uses the standard BankKeeper)
	// can see balance changes made by the giga executor via GigaBankKeeper.
	if app.GigaExecutorEnabled {
		ctx.GigaMultiStore().WriteGiga()
	}

	app.EvmKeeper.SetTxResults(txResults)
	app.EvmKeeper.SetMsgs(evmTxs)

	// Finalize all Bank Module Transfers here so that events are included
	lazyWriteEvents := app.BankKeeper.WriteDeferredBalances(ctx)
	events = append(events, lazyWriteEvents...)

	// Sum up total used per block only for evm transactions
	var evmTotalGasUsed int64
	for _, txResult := range txResults {
		if txResult.EvmTxInfo != nil {
			evmTotalGasUsed += txResult.GasUsed
		}
	}

	endBlockResp = app.EndBlock(ctx, req.Height, evmTotalGasUsed)

	events = append(events, endBlockResp.Events...)
	return events, txResults, endBlockResp, nil
```

**File:** app/app.go (L2609-2612)
```go
// checkTotalBlockGas checks that the block gas limit is not exceeded by our best estimate of
// the total gas by the txs in the block. The gas of a tx is either the gas estimate if it's an EVM tx,
// or the gas wanted if it's a Cosmos tx. typedTxs must align with proposal order (nil = decode failure).
func (app *App) checkTotalBlockGas(ctx sdk.Context, typedTxs []sdk.Tx) (_result bool) {
```

**File:** app/app.go (L2634-2667)
```go
		var gasWanted uint64
		if isEVM {
			msg := evmtypes.MustGetEVMTransactionMessage(decodedTx)
			if msg.IsAssociateTx() {
				continue
			}
			etx, _ := msg.AsTransaction()
			gasWanted = etx.Gas()
		} else {
			feeTx, ok := decodedTx.(sdk.FeeTx)
			if !ok {
				// Non-fee tx won't be processed and thus won't consume gas. Skipping.
				continue
			}
			gasWanted = feeTx.GetGas()
		}

		// Overflow guards: gasWanted must fit in int64, and adding it to either accumulator
		// must not wrap uint64.
		if int64(gasWanted) < 0 || //nolint:gosec
			totalGasWanted > math.MaxUint64-gasWanted ||
			totalGas > math.MaxUint64-gasWanted {
			return false
		}

		totalGasWanted += gasWanted

		// Prefer the gas estimate when it's a valid EVM estimate (>= MinGasEVMTx) and not
		// inflated above gasWanted; otherwise charge full gasWanted.
		if est := decodedTx.GetGasEstimate(); est >= MinGasEVMTx && est <= gasWanted {
			totalGas += est
		} else {
			totalGas += gasWanted
		}
```

**File:** sei-cosmos/types/tx_msg.go (L38-49)
```go
	// Tx defines the interface a transaction must fulfill.
	Tx interface {
		// Gets the all the transaction's messages.
		GetMsgs() []Msg

		// ValidateBasic does a simple and lightweight validation check that doesn't
		// require access to any other information.
		ValidateBasic() error

		// GetGasEstimate returns the estimated gas used by the transaction.
		GetGasEstimate() uint64
	}
```

**File:** x/evm/ante/gas.go (L24-45)
```go
// Called at the end of the ante chain to set gas limit and gas used estimate properly
func (gl GasDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	txData, err := evmtypes.UnpackTxData(msg.Data)
	if err != nil {
		return ctx, err
	}

	txGas := txData.GetGas()
	if txGas > math.MaxInt64 {
		return ctx, errors.New("tx gas exceeds max")
	}
	adjustedGasLimit := gl.evmKeeper.GetPriorityNormalizer(ctx).MulInt64(int64(txGas)) //nolint:gosec
	gasMeter := sdk.NewGasMeterWithMultiplier(ctx, adjustedGasLimit.TruncateInt().Uint64())
	ctx = ctx.WithGasMeter(gasMeter)
	if tx.GetGasEstimate() >= MinGasEVMTx {
		ctx = ctx.WithGasEstimate(tx.GetGasEstimate())
	} else {
		ctx = ctx.WithGasEstimate(gasMeter.Limit())
	}
	return next(ctx, tx, simulate)
}
```
