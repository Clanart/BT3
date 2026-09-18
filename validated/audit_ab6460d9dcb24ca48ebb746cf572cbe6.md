### Title
Stack trace disclosure in transaction error responses to any transaction sender - ([File: sei-cosmos/baseapp/recovery.go])

### Summary
When a submitted transaction causes an unhandled panic during `CheckTx`/`DeliverTx` execution (and equivalently during EVM tx execution via the giga executor path), the default panic-recovery handler embeds the full Go runtime stack trace directly into the error message that becomes the ABCI response `Log`. This `Log` value is propagated verbatim to the client as `TxResponse.RawLog`, exposing internal file paths, function names, and call-stack details to any unprivileged transaction sender or public RPC client — the same bug class as CVE-2017-3154 (Apache Atlas exposing stack traces in error responses).

### Finding Description
`newDefaultRecoveryMiddleware` is the last-resort recovery handler in the `runTx` panic-recovery middleware chain. On any unclassified panic, it wraps the recovered value together with `debug.Stack()` into the error message: [1](#0-0) 

This mirrors the identical pattern used in the giga fast-path EVM delivery function, where any unclassified panic (e.g., "nil deref from malformed protobuf") is caught and the response `Log` is populated with `fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack()))`: [2](#0-1) 

The resulting `Log`/error string flows unmodified through `sdkerrors.ABCIInfo` into `ResponseCheckTx.Log` / `ResponseDeliverTx.Log`, and from there into `TxResponse.RawLog`, which is what any RPC client receives when broadcasting a transaction synchronously or querying a transaction by hash: [3](#0-2) [4](#0-3) [5](#0-4) 

The recovery chain (`newOutOfGasRecoveryMiddleware`, `newOCCAbortRecoveryMiddleware`, `newContextCancelledRecoveryMiddleware`, `newDefaultRecoveryMiddleware`) is invoked from `runTx` in `sei-cosmos/baseapp/baseapp.go` and from the legacy ABCI `check_tx.go`/`deliver_tx.go` wiring, confirming this is on the standard transaction-processing path reachable from any submitted transaction, not a debug-only or admin-only code path.

### Impact Explanation
Any unprivileged party who can construct a transaction that triggers an unhandled panic during execution (e.g., a malformed/edge-case EVM message, a nil-dereference from unexpected protobuf content, or any other non-`ErrorOutOfGas`/non-OCC-abort panic) will have the complete internal stack trace — including internal package paths, function names, and call sequence — returned in the transaction's `RawLog` field to the broadcasting client and to anyone querying that transaction. This is an information-disclosure vulnerability (CWE-200): it does not directly cause fund loss but materially aids an attacker in reverse-engineering internal code structure, identifying further panic-inducing conditions, and refining exploit attempts against the EVM/Cosmos execution pipeline (e.g., precompiles, giga executor, OCC scheduling). Because Sei is a public chain with public RPC endpoints, this information leaks to any public-RPC client.

### Likelihood Explanation
Likelihood is high for information disclosure: panics in transaction execution are a realistic occurrence given the complexity of the EVM/Cosmos interop layer (pointer contracts, precompiles, giga OCC parallel execution), and the recovery/fallback code paths explicitly acknowledge this ("For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic"). No special privilege is required — a normal transaction sender only needs to trigger any panic not already specifically classified (OOG, OCC abort, context cancellation, iterator-unsupported).

### Recommendation
- In `newDefaultRecoveryMiddleware` (sei-cosmos/baseapp/recovery.go) and in the giga `makeGigaDeliverTx` panic handler (app/app.go), stop embedding `string(debug.Stack())` into the value returned to the client (`Log`/error message). Log the stack trace server-side only (as already done via `logger.Error(...)`), and return a generic, non-identifying error message (e.g., `"internal error"` plus an opaque error/trace ID) in the ABCI response `Log`/`Data` fields.
- Audit all other locations that build ABCI response `Log` fields from recovered panics for the same pattern to ensure none leak `debug.Stack()` output into client-visible fields.

### Proof of Concept
1. Submit an EVM (or Cosmos) transaction crafted to trigger an unclassified panic during execution — e.g., a message reaching a nil-pointer dereference in a precompile/pointer path, or malformed protobuf content that the giga executor cannot parse defensively (as anticipated by the comment "e.g., nil deref from malformed protobuf" in `app/app.go`).
2. The panic is caught by the top-level `recover()`; since it is not an `occ.Abort`, `sdk.ErrorOutOfGas`, or `gigastore.ErrIteratorUnsupported`, it falls to the default branch:
   `resp.Log = fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack()))` (app/app.go:2126-2132) or the equivalent `newDefaultRecoveryMiddleware` path for the standard `runTx` flow.
3. Broadcast the transaction via `broadcast_tx_commit` or query it afterwards via `tx` query; the returned `TxResponse.RawLog` (populated straight from `ResponseDeliverTx.Log`, per `sei-cosmos/types/result.go`) contains the full internal stack trace, disclosing internal package/function names and call paths to the requester.

### Citations

**File:** sei-cosmos/baseapp/recovery.go (L113-123)
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

**File:** sei-cosmos/types/result.go (L61-84)
```go
// NewResponseResultTx returns a TxResponse given a ResultTx from tendermint
func NewResponseResultTx(res *ctypes.ResultTx, anyTx *codectypes.Any, timestamp string) *TxResponse {
	if res == nil {
		return nil
	}

	parsedLogs, _ := ParseABCILogs(res.TxResult.Log)

	return &TxResponse{
		TxHash:    res.Hash.String(),
		Height:    res.Height,
		Codespace: res.TxResult.Codespace,
		Code:      res.TxResult.Code,
		Data:      strings.ToUpper(hex.EncodeToString(res.TxResult.Data)),
		RawLog:    res.TxResult.Log,
		Logs:      parsedLogs,
		Info:      res.TxResult.Info,
		GasWanted: res.TxResult.GasWanted,
		GasUsed:   res.TxResult.GasUsed,
		Tx:        anyTx,
		Timestamp: timestamp,
		Events:    res.TxResult.Events,
	}
}
```

**File:** sei-cosmos/types/result.go (L124-149)
```go
func newTxResponseDeliverTx(res *ctypes.ResultBroadcastTxCommit) *TxResponse {
	if res == nil {
		return nil
	}

	var txHash string
	if res.Hash != nil {
		txHash = res.Hash.String()
	}

	parsedLogs, _ := ParseABCILogs(res.TxResult.Log)

	return &TxResponse{
		Height:    res.Height,
		TxHash:    txHash,
		Codespace: res.TxResult.Codespace,
		Code:      res.TxResult.Code,
		Data:      strings.ToUpper(hex.EncodeToString(res.TxResult.Data)),
		RawLog:    res.TxResult.Log,
		Logs:      parsedLogs,
		Info:      res.TxResult.Info,
		GasWanted: res.TxResult.GasWanted,
		GasUsed:   res.TxResult.GasUsed,
		Events:    res.TxResult.Events,
	}
}
```

**File:** sei-cosmos/types/result.go (L151-167)
```go
// NewResponseFormatBroadcastTx returns a TxResponse given a ResultBroadcastTx from tendermint
func NewResponseFormatBroadcastTx(res *ctypes.ResultBroadcastTx) *TxResponse {
	if res == nil {
		return nil
	}

	parsedLogs, _ := ParseABCILogs(res.Log)

	return &TxResponse{
		Code:      res.Code,
		Codespace: res.Codespace,
		Data:      res.Data.String(),
		RawLog:    res.Log,
		Logs:      parsedLogs,
		TxHash:    res.Hash.String(),
	}
}
```
