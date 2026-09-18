### Title
Panic recovery in CheckTx/DeliverTx leaks full server stack traces to unprivileged transaction senders - ([File: app/legacyabci/recovery.go])

### Summary
The legacy ABCI panic-recovery path bakes a full Go runtime stack trace (including internal file paths and line numbers) directly into the error message text returned to the transaction submitter, regardless of any debug/trace configuration flag. This is the same bug class as CVE-2025-66422 (trytond leaking traceback/server-setup information to remote, unprivileged callers): a public-facing tx/RPC endpoint returns internal diagnostic information (stack traces revealing code paths, module layout, and internals) that should be confined to server-side logs.

### Finding Description
`newDefaultRecoveryMiddleware` in [1](#0-0)  builds the panic-recovery error as:
```go
sdkerrors.Wrap(sdkerrors.ErrPanic, fmt.Sprintf("recovered: %v\nstack:\n%v", recoveryObj, string(debug.Stack())))
```
Unlike the Cosmos SDK's designed mechanism for hiding stack traces from non-debug clients — `ABCIInfo(err, debug)` in [2](#0-1)  which only calls `debugErrEncoder` (full `%+v`, including `pkg/errors` stacktrace metadata) when `debug == true`, and otherwise calls `defaultErrEncoder` which is just `err.Error()` — the panic-recovery text here embeds `debug.Stack()` output directly into the wrapped error's *description string*. That string becomes part of `err.Error()` itself, so `defaultErrEncoder(err)` (used when `debug=false`, the production default) still returns the entire stack trace, because the trace was never attached as detachable stacktrace metadata — it was concatenated into the message text.

This recovery middleware is invoked from `CheckTx` in [3](#0-2)  on any panic during ante-handler/message processing, and analogous default recovery/log logic exists for `DeliverTx`/`ProcessBlock` paths as well (e.g. [4](#0-3)  and [5](#0-4) , both of which also format `Log` with `fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack()))`).

The resulting error/response `Log` field is exactly what gets shipped back over the public RPC surface: `CheckTx`/`DeliverTx` results flow to `ResponseCheckTx.Log` / `ResponseDeliverTx.Log`, which Tendermint's `broadcast_tx_sync`/`broadcast_tx_commit` handlers return verbatim to the caller (see `BroadcastTx`/`BroadcastTxCommit` in [6](#0-5) , and further surfaced via `sdk.TxResponse.RawLog` in [7](#0-6) ), and the JSON-RPC/HTTP transport writes this text straight to the client in the response body (`writeRPCResponse`/`writeHTTPResponse` in `sei-tendermint/rpc/jsonrpc/server/http_server.go`).

### Impact Explanation
Any unprivileged party who can craft a transaction that triggers a Go panic during ante-handling or message execution (e.g. malformed protobuf triggering a nil-dereference, as explicitly anticipated by the code comment "For other panics (e.g., nil deref from malformed protobuf)" in [8](#0-7) ) receives, in the transaction's `Log`/`RawLog` field, a complete Go stack trace: internal package paths, function names, and file/line numbers of the running node's binary. This discloses internal server implementation details (module structure, build paths, active code paths, potentially version-specific internals) to any remote, unauthenticated caller of the public RPC/tx-broadcast interface — directly analogous to the "obtain sensitive trace-back (server setup) information" issue in the trytond advisory. It does not itself cause fund loss, consensus divergence, or node crash, but is a genuine information-disclosure weakness (CWE-402-class) reachable by any public RPC client submitting an unprivileged transaction.

### Likelihood Explanation
Triggering this requires only finding one panic-inducing transaction shape reachable through `CheckTx`/ante processing/message handling — the codebase's own comments acknowledge such panics occur in practice (malformed protobuf → nil deref), and the recovery path is a broad catch-all covering "other panics," so exploitation likelihood is non-trivial for any public node with an open tx-broadcast/JSON-RPC endpoint (the default node configuration).

### Recommendation
Do not concatenate `debug.Stack()` into the error message/description text that becomes the client-visible `Log`. Instead:
- Log the stack trace server-side only (already done via `logger.Error(..., "stack", string(debug.Stack()))`), and
- Return to the client a generic message (e.g. just `sdkerrors.ErrPanic` with the panic value, no stack) unless an explicit node-operator `--trace`/debug flag is enabled, mirroring the `ABCIInfo(err, debug)` pattern already used for query results.

### Proof of Concept
1. Craft and submit an EVM or Cosmos transaction whose protobuf/msg content causes a nil-pointer dereference or other panic during ante-handling or message execution on `CheckTx` (the code explicitly notes malformed-protobuf-induced nil derefs as a realistic trigger).
2. Submit via public `broadcast_tx_sync`/`broadcast_tx_commit` RPC.
3. Observe the returned `Log`/`RawLog` field contains `"recovered: <panic>\nstack:\n<full Go stack trace>"`, exposing internal file paths and function names of the node's server implementation to the (unprivileged) caller.

### Citations

**File:** app/legacyabci/recovery.go (L86-96)
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

**File:** sei-cosmos/types/errors/abci.go (L38-49)
```go
func ABCIInfo(err error, debug bool) (codespace string, code uint32, log string) {
	if errIsNil(err) {
		return "", SuccessABCICode, ""
	}

	encode := defaultErrEncoder
	if debug {
		encode = debugErrEncoder
	}

	return abciCodespace(err), abciCode(err), encode(err)
}
```

**File:** app/legacyabci/check_tx.go (L66-75)
```go
	defer func() {
		if r := recover(); r != nil {
			recoveryMW := newOutOfGasRecoveryMiddleware(gasWanted, ctx, defaultRecoveryMiddleware)
			err, result = processRecovery(r, recoveryMW), nil
		}
		if ctx.GasMeter() == blockGasMeter {
			return
		}
		gInfo = sdk.GasInfo{GasWanted: gasWanted, GasUsed: ctx.GasMeter().GasConsumed(), GasEstimate: gasEstimate}
	}()
```

**File:** app/app.go (L1764-1781)
```go
func (app *App) ProcessBlock(ctx sdk.Context, txs [][]byte, req *BlockProcessRequest, lastCommit abci.CommitInfo, simulate bool, preDecoded []sdk.Tx) (events []abci.Event, txResults []*abci.ExecTxResult, endBlockResp abci.ResponseEndBlock, err error) {
	defer func() {
		if r := recover(); r != nil {
			panicMsg := fmt.Sprintf("%v", r)

			// Re-panic for upgrade-related panics to allow proper upgrade mechanism
			if upgradePanicRe.MatchString(panicMsg) {
				logger.Error("upgrade panic detected, panicking to trigger upgrade", "panic", r)
				panic(r) // Re-panic to trigger upgrade mechanism
			}
			stack := string(debug.Stack())
			logger.Error("panic recovered in ProcessBlock", "panic", r, "stack", stack)
			err = fmt.Errorf("ProcessBlock panic: %v", r)
			events = nil
			txResults = nil
			endBlockResp = abci.ResponseEndBlock{}
		}
	}()
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

**File:** sei-tendermint/internal/rpc/core/mempool.go (L87-123)
```go
func (env *Environment) BroadcastTx(ctx context.Context, req *coretypes.RequestBroadcastTx) (*coretypes.ResultBroadcastTx, error) {
	if err := env.requireWritable(); err != nil {
		return nil, err
	}
	if giga, ok := env.gigaRouter().Get(); ok {
		v, ok := giga.Mempool().Get()
		if !ok {
			return nil, errors.New("autobahn fullnode has no local mempool; broadcast_tx_* must be sent to a validator")
		}
		r, err := v.InsertTx(ctx, req.Tx)
		if err != nil {
			return nil, err
		}
		return &coretypes.ResultBroadcastTx{
			Code:      r.Code,
			Data:      r.Data,
			Codespace: r.Codespace,
			Hash:      req.Tx.Hash().Bytes(),
			Log:       r.Log,
		}, nil
	}
	mp, err := env.requireMempool()
	if err != nil {
		return nil, err
	}
	r, err := mp.CheckTx(ctx, req.Tx)
	if err != nil {
		return nil, err
	}
	return &coretypes.ResultBroadcastTx{
		Code:      r.Code,
		Data:      r.Data,
		Codespace: r.Codespace,
		Hash:      req.Tx.Hash().Bytes(),
		Log:       r.Log,
	}, nil
}
```

**File:** sei-cosmos/types/result.go (L100-122)
```go
func newTxResponseCheckTx(res *ctypes.ResultBroadcastTxCommit) *TxResponse {
	if res == nil {
		return nil
	}

	var txHash string
	if res.Hash != nil {
		txHash = res.Hash.String()
	}

	parsedLogs, _ := ParseABCILogs(res.CheckTx.Log)

	return &TxResponse{
		Height:    res.Height,
		TxHash:    txHash,
		Codespace: res.CheckTx.Codespace,
		Code:      res.CheckTx.Code,
		Data:      strings.ToUpper(hex.EncodeToString(res.CheckTx.Data)),
		RawLog:    res.CheckTx.Log,
		Logs:      parsedLogs,
		GasWanted: res.CheckTx.GasWanted,
	}
}
```
