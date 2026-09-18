### Title
Full Go panic stack traces are written into the client-visible `ResponseDeliverTx.Log` field in the giga fast-path, bypassing the panic-redaction mechanism - (File: app/app.go)

### Summary
When a giga-executed EVM transaction panics inside `makeGigaDeliverTx`, the recovery handler builds the ABCI `ResponseDeliverTx.Log` directly from `fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack()))`, embedding the complete Go runtime stack trace (internal file paths, package/function names, goroutine internals) into a field that becomes part of the permanently-queryable, publicly-readable transaction result. This bypasses the `ErrPanic` redaction path that the legacy/V2 delivery pipeline relies on to withhold such details from untrusted callers, mirroring the class of bug in the reported MoinMoin advisory: sensitive internal diagnostic information being exposed through a normal, unprivileged-reachable interface instead of being confined to operator-only logs.

### Finding Description
The legacy ABCI pipeline is designed so that a recovered panic is wrapped as `sdkerrors.ErrPanic`, whose declaration explicitly states the intent to redact: [1](#0-0) 

That wrapped error still carries the full "recovered: ...\nstack:\n..." message at construction time: [2](#0-1) [3](#0-2) 

but it is only surfaced to the ABCI response through `ABCIInfo(err, debug)`, which is fed the app's `trace` config flag (`false` in default/production configuration): [4](#0-3) [5](#0-4) 

This is the intended control point where the raw stack trace is expected to be dropped/redacted for non-debug nodes before it reaches `ResponseDeliverTx.Log`.

However, the giga fast execution path in `app/app.go` constructs the `ResponseDeliverTx`/`ExecTxResult` directly on panic recovery, without ever routing through `ABCIInfo`/`app.trace`: [6](#0-5) 

This is the deliver-tx entry point used per-transaction by the giga executor: [7](#0-6) 

The parallel synchronous-giga path is safer for the exact same panic class — it only logs the stack server-side and returns `"panic recovered: %v"` (no stack) to the client — showing the intended, safe behavior that `makeGigaDeliverTx` fails to replicate: [8](#0-7) 

Because giga is the executor used for ordinary EVM transaction delivery, any transaction that reaches a non-`OutOfGas`, non-`OCCAbort`, non-`ErrIteratorUnsupported` panic (e.g., a nil dereference triggered by malformed/edge-case protobuf or state) will have its stack trace placed verbatim into `ResponseDeliverTx.Log`.

### Impact Explanation
`ResponseDeliverTx.Log` is not merely a server log file — it is committed into the block's tx results and is retrievable indefinitely by any unprivileged client via standard public RPC (`tx`, `tx_search`, CheckTx/DeliverTx events, and light-client proofs). Exposing a raw Go stack trace here leaks internal implementation details (source file layout, function names, package paths, potentially version-correlated line numbers) to any observer, which is directly analogous to the MoinMoin CVE-2007-0902 issue of debug tracebacks disclosing internal system information through a supposedly production-safe interface. This qualifies as CWE-532 (Insertion of Sensitive Information into Log File/Externally-Observable Output) at Medium severity — it does not itself cause fund loss but does provide reconnaissance information that can materially aid follow-on exploitation of other panic-triggering bugs.

### Likelihood Explanation
Any unprivileged EVM transaction sender who can trigger a panic inside `executeEVMTxWithGigaExecutor` (any panic that is not `sdk.ErrorOutOfGas`, `occ.Abort`, or wrapping `gigastore.ErrIteratorUnsupported`) automatically has this diagnostic message returned in their own transaction's queryable result — no special access or validator collusion required. The comment in `app/app.go` itself acknowledges these are reachable via "malformed protobuf" or similar client-triggerable conditions, making likelihood non-trivial for a chain that processes untrusted EVM traffic at scale.

### Recommendation
Route the giga panic-recovery default branch in `makeGigaDeliverTx` (and any other giga/synchronous fast-path panic handlers) through the same `sdkerrors.ErrPanic` + `ABCIInfo(err, app.trace)` mechanism used by the legacy pipeline, so the stack trace is only included when the node has explicitly opted into debug/trace mode. At minimum, strip `debug.Stack()` from the client-visible `Log` field and retain it only in the server-side `logger.Error` call, matching the pattern already used in `ProcessTxsSynchronousGiga`.

### Proof of Concept
1. Submit an EVM transaction (via `eth_sendRawTransaction`) crafted to reach a code path in `executeEVMTxWithGigaExecutor` that dereferences a nil pointer or otherwise panics outside the three explicitly-handled panic types (`ErrorOutOfGas`, `occ.Abort`, `gigastore.ErrIteratorUnsupported`) — the changelog reference to "nil deref from malformed protobuf" in the surrounding code comments indicates this class of panic is reachable from client-supplied transaction data.
2. The transaction is delivered via `makeGigaDeliverTx`; the panic is recovered and `resp.Log` is set to `fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack()))`.
3. Query the transaction result afterward via the public `tx` or `tx_search` RPC endpoint; the returned `Log` field contains the full internal Go stack trace, confirming disclosure to an unprivileged caller.

### Citations

**File:** sei-cosmos/types/errors/errors.go (L165-167)
```go
	// ErrPanic is only set when we recover from a panic, so we know to
	// redact potentially sensitive system info
	ErrPanic = Register(UndefinedCodespace, 111222, "panic")
```

**File:** sei-cosmos/baseapp/recovery.go (L113-124)
```go
// newDefaultRecoveryMiddleware creates a default (last in chain) recovery middleware for app.runTx method.
func newDefaultRecoveryMiddleware() recoveryMiddleware {
	handler := func(recoveryObj interface{}) error {
		return sdkerrors.Wrap(
			sdkerrors.ErrPanic, fmt.Sprintf(
				"recovered: %v\nstack:\n%v", recoveryObj, string(debug.Stack()),
			),
		)
	}

	return newRecoveryMiddleware(handler, nil)
}
```

**File:** app/legacyabci/recovery.go (L86-97)
```go
// newDefaultRecoveryMiddleware creates a default (last in chain) recovery middleware for app.runTx method.
func newDefaultRecoveryMiddleware() recoveryMiddleware {
	handler := func(recoveryObj interface{}) error {
		return sdkerrors.Wrap(
			sdkerrors.ErrPanic, fmt.Sprintf(
				"recovered: %v\nstack:\n%v", recoveryObj, string(debug.Stack()),
			),
		)
	}

	return newRecoveryMiddleware(handler, nil)
}
```

**File:** sei-cosmos/baseapp/abci.go (L198-208)
```go
	runTxRes, err := app.runTx(ctx.WithTxBytes(req.Tx).WithTxSum(checksum), runTxModeDeliver, tx, checksum)
	gInfo = runTxRes.gasInfo
	result := runTxRes.result
	if err != nil {
		resultStr = "failed"
		// if we have a result, use those events instead of just the anteEvents
		if result != nil {
			return sdkerrors.ResponseDeliverTxWithEvents(err, gInfo.GasWanted, gInfo.GasUsed, sdk.MarkEventsToIndex(result.Events, app.IndexEvents), app.trace)
		}
		return sdkerrors.ResponseDeliverTxWithEvents(err, gInfo.GasWanted, gInfo.GasUsed, sdk.MarkEventsToIndex(runTxRes.anteEvents, app.IndexEvents), app.trace)
	}
```

**File:** sei-cosmos/types/errors/abci.go (L64-76)
```go
// ResponseDeliverTxWithEvents returns an ABCI ResponseDeliverTx object with fields filled in
// from the given error, gas values and events.
func ResponseDeliverTxWithEvents(err error, gw, gu uint64, events []abci.Event, debug bool) abci.ResponseDeliverTx {
	space, code, log := ABCIInfo(err, debug)
	return abci.ResponseDeliverTx{
		Codespace: space,
		Code:      code,
		Log:       log,
		GasWanted: safeIntFromUint64(gw),
		GasUsed:   safeIntFromUint64(gu),
		Events:    events,
	}
}
```

**File:** app/app.go (L1452-1458)
```go
					// For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic
					logger.Error("panic in giga synchronous executor", "panic", r, "stack", string(debug.Stack()))
					result = &abci.ExecTxResult{
						Codespace: sdkerrors.UndefinedCodespace,
						Code:      sdkerrors.ErrPanic.ABCICode(),
						Log:       fmt.Sprintf("panic recovered: %v", r),
					}
```

**File:** app/app.go (L2095-2096)
```go
func (app *App) makeGigaDeliverTx(cache *gigaBlockCache) func(sdk.Context, abci.RequestDeliverTxV2, sdk.Tx, [32]byte) abci.ResponseDeliverTx {
	return func(ctx sdk.Context, req abci.RequestDeliverTxV2, tx sdk.Tx, checksum [32]byte) (resp abci.ResponseDeliverTx) {
```

**File:** app/app.go (L2126-2132)
```go
				// For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic
				logger.Error("panic in gigaDeliverTx", "panic", r, "stack", string(debug.Stack()))
				resp = abci.ResponseDeliverTx{
					Codespace: sdkerrors.UndefinedCodespace,
					Code:      sdkerrors.ErrPanic.ABCICode(),
					Log:       fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack())),
				}
```
