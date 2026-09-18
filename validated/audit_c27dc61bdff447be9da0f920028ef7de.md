Found a strong analog. In `x/evm/ante/basic.go`, `BasicDecorator.AnteHandle` calls `etx, _ := msg.AsTransaction()` and discards the error, then immediately dereferences `etx` (`etx.To()`, `etx.Value()`, `etx.Data()`, `etx.Gas()`, `etx.Type()`, `etx.AccessList()`) without checking whether `etx` is `nil`. [1](#0-0) [2](#0-1) 

`MsgEVMTransaction.AsTransaction()` returns `nil, nil` whenever `UnpackTxData(msg.Data)` fails to unpack the packed `Any` into a recognized `ethtx.TxData` variant — this is exactly analogous to the CVE's "mandatory field missing → nil returned → nil dereferenced" pattern: [3](#0-2) 

### Title
Nil-pointer dereference in EVM ante `BasicDecorator` on malformed `MsgEVMTransaction.Data` - (File: x/evm/ante/basic.go)

### Summary
`BasicDecorator.AnteHandle`, part of the EVM-specific ante chain wired into `NewAnteHandler`, discards the error from `msg.AsTransaction()` and unconditionally dereferences the resulting `*ethtypes.Transaction`. If `UnpackTxData` fails to produce a valid `ethtx.TxData`, `AsTransaction()` returns `(nil, nil)`, and the very next lines call `etx.To()`, `etx.Value()`, `etx.Data()`, etc., panicking with a nil-pointer dereference.

### Finding Description
`AsTransaction()` is defensively written to *not* panic on unpack failure — it returns `nil` instead: [3](#0-2) 

But every caller that discards the error assumes a non-nil result. `BasicDecorator.AnteHandle` is exactly such a caller, in the `evmAnteDecorators` chain used for both CheckTx and DeliverTx of any `MsgEVMTransaction`: [4](#0-3) 

The decorator order matters: `NewEVMPreprocessDecorator` runs before `NewBasicDecorator`, but neither guarantees `msg.Data` unpacks to a supported `ethtx.TxData` type that survives `AsTransaction()`. `ValidateBasic()` on the message does call `UnpackTxData` and returns an error on failure, and normally `ValidateBasicDecorator` runs earlier in the base ante chain, but the EVM router splits transactions into a separate `evmAnteHandler` chain (`evmAnteDecorators`) that does **not** include `ante.NewValidateBasicDecorator()` — that decorator only exists in the default (non-EVM) `anteDecorators` list: [5](#0-4) 

So for the EVM path, nothing in `evmAnteDecorators` before `BasicDecorator` guarantees `ValidateBasic` was invoked with an error check that would short-circuit before `BasicDecorator` runs. If any code path reaches `BasicDecorator.AnteHandle` with a `MsgEVMTransaction.Data` whose registered type does not implement `ethtx.TxData` cleanly, or whose interface unpacking otherwise fails at this later point (e.g. due to a mismatch between the interface registered at `ValidateBasic` time vs. runtime, or a future variant added without updating `UnpackTxData`), the ante handler panics.

### Impact Explanation
A panic inside the ante handler is recovered by `runTx`'s `defer recover()` in `sei-cosmos/baseapp/baseapp.go`, so a single malformed transaction does not crash the whole node process by itself — this differs from the referenced CVE where the panic terminates the process outright. [6](#0-5)  That materially reduces the severity relative to the CVE (no full node crash from a single request), and the validate/impact bar set by the rules (crash of default-configuration RPC nodes, block delay beyond 2.5s, or validator halt) is not clearly met here since the panic is caught. I cannot confirm a path where this nil dereference escapes the recovery net (e.g., in `CheckTx`'s ABCI wrapper, which also recovers in `app/legacyabci/check_tx.go`).

### Likelihood Explanation
Reaching this exact `nil, nil` return requires constructing a `MsgEVMTransaction` whose `Data` `Any` decodes into a type registered as `ethtx.TxData` at the interface-registry level (so `UnpackAny` succeeds structurally) but for which `UnpackTxData` still errors — I was not able to confirm a concrete unregistered/malformed payload that clears `ValidateBasic` (called earlier in normal flow) yet still trips this later, unguarded call. Given the code's redundant `ValidateBasic` call earlier in `MsgEVMTransaction.ValidateBasic()`, the likelihood of an attacker-reachable trigger for this exact nil-deref (bypassing that check) is uncertain without deeper tracing of the EVM router / interceptor plumbing.

### Recommendation
In `x/evm/ante/basic.go`, check the discarded error from `msg.AsTransaction()` (or check `etx == nil`) and return an ante error instead of proceeding to dereference `etx`. Apply the same fix to any other caller (e.g. `x/evm/ante/fee.go`, `x/evm/keeper/msg_server.go`, `giga/deps/xevm/keeper/deferred.go`) that discards this error via `etx, _ := msg.AsTransaction()`.

### Proof of Concept
I could not construct a concrete, verified end-to-end transaction payload that reaches `BasicDecorator.AnteHandle` with `msg.Data` unpacking to `nil` while bypassing the earlier `ValidateBasic` check, given the available index. This finding should be treated as a **defensive-coding gap with an unconfirmed trigger** rather than a proven exploitable crash; a Devin session with full repository/build access would be needed to trace all `UnpackTxData` registration paths and interceptor call order to confirm or refute reachability from an unprivileged EVM transaction sender.

### Citations

**File:** x/evm/ante/basic.go (L26-27)
```go
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	etx, _ := msg.AsTransaction()
```

**File:** x/evm/ante/basic.go (L43-59)
```go
	if etx.To() == nil && len(etx.Data()) > params.MaxInitCodeSize {
		return ctx, fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(etx.Data()), params.MaxInitCodeSize)
	}

	if etx.Value().Sign() < 0 {
		return ctx, sdkerrors.ErrInvalidCoins
	}

	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return ctx, err
	}
	if etx.Gas() < intrGas {
		return ctx, core.ErrIntrinsicGas
	}

	if etx.Type() == ethtypes.BlobTxType {
```

**File:** x/evm/types/message_evm_transaction.go (L67-74)
```go
func (msg *MsgEVMTransaction) AsTransaction() (*ethtypes.Transaction, ethtx.TxData) {
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return nil, nil
	}

	return ethtypes.NewTx(txData.AsEthereumData()), txData
}
```

**File:** app/ante.go (L66-100)
```go
	sequentialVerifyDecorator := ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler)

	anteDecorators := []sdk.AnteDecorator{
		ante.NewSetUpContextDecorator(antedecorators.GetGasMeterSetter(options.ParamsKeeper.(paramskeeper.Keeper))), // outermost AnteDecorator. SetUpContext must be called first
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.ParamsKeeper.(paramskeeper.Keeper), options.TxFeeChecker),
		wasmkeeper.NewLimitSimulationGasDecorator(options.WasmConfig.SimulationGasLimit, wasmkeeper.DefaultGasMeterSetter()), // after setup context to enforce limits early
		ante.NewRejectExtensionOptionsDecorator(),
		ante.NewValidateBasicDecorator(),
		ante.NewTxTimeoutHeightDecorator(),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		// PriorityDecorator must be called after DeductFeeDecorator which sets tx priority based on tx fees
		antedecorators.NewPriorityDecorator(),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, sigGasConsumer),
		sequentialVerifyDecorator,
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
		evmante.NewEVMAddressDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		antedecorators.NewAuthzNestedMessageDecorator(),
	}

	anteHandler := sdk.ChainAnteDecorators(anteDecorators...)

	evmAnteDecorators := []sdk.AnteDecorator{
		// NOTE: NewEVMNoCosmosFieldsDecorator must come first to prevent writing state to chain without being charged.
		// E.g. EVMPreprocessDecorator may short-circuit all the later ante handlers if AssociateTx and ignore NewEVMNoCosmosFieldsDecorator.
		evmante.NewEVMNoCosmosFieldsDecorator(),
		evmante.NewEVMPreprocessDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		evmante.NewBasicDecorator(options.EVMKeeper),
		evmante.NewEVMFeeCheckDecorator(options.EVMKeeper, options.UpgradeKeeper),
		evmante.NewEVMSigVerifyDecorator(options.EVMKeeper, options.LatestCtxGetter),
		evmante.NewGasDecorator(options.EVMKeeper),
	}
```

**File:** sei-cosmos/baseapp/baseapp.go (L903-915)
```go
	blockGasMeter := ctx.GasMeter()
	defer func() {
		if r := recover(); r != nil {
			recoveryMW := newContextCancelledRecoveryMiddleware(ctx, app.runTxRecoveryMiddleware)
			recoveryMW = newOutOfGasRecoveryMiddleware(gasWanted, ctx, recoveryMW)
			recoveryMW = newOCCAbortRecoveryMiddleware(recoveryMW) // TODO: do we have to wrap with occ enabled check?
			err, runTxRes.result = processRecovery(r, recoveryMW), nil
		}
		if ctx.GasMeter() == blockGasMeter {
			return
		}
		runTxRes.gasInfo = sdk.GasInfo{GasWanted: gasWanted, GasUsed: ctx.GasMeter().GasConsumed(), GasEstimate: gasEstimate}
	}()
```
