### Title
Unhandled-panic recovery in transaction execution leaks internal Go source file paths (stack trace) to any transaction sender — ([File: app/app.go])

### Summary
When a submitted transaction triggers an unrecovered panic during `DeliverTx`/giga execution (e.g. a malformed EVM transaction protobuf causing a nil-pointer dereference), the recovery handler embeds the full `runtime/debug.Stack()` output — including absolute Go source file paths of the node's build — directly into the ABCI response `Log` field that is returned for that transaction. This is the same bug class as CVE-2019-6792: an error path unconditionally surfaces internal instance/filesystem information to an untrusted, unprivileged caller.

### Finding Description
`app.makeGigaDeliverTx`'s panic-recovery `defer` unconditionally formats the raw panic value together with the full stack trace into the transaction's `Log` field, with no debug/trace gate: [1](#0-0) 

The same unconditional pattern exists in the synchronous giga path: [2](#0-1) 

and in the legacy ABCI recovery middleware used for the non-giga `DeliverTx`/`CheckTx` flow: [3](#0-2) 

and again in `sei-cosmos/baseapp/recovery.go`: [4](#0-3) 

`debug.Stack()` prints full source-file paths for every frame (e.g. the build machine's Go module cache path and the exact `sei-chain` internal package layout). The comment right above the `makeGigaDeliverTx` recovery block explicitly acknowledges that ordinary, non-malicious-proposer input can reach this branch: *"For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic"* — i.e., a single unprivileged sender's crafted `MsgEVMTransaction` payload is a plausible trigger, not a validator/consensus-message issue.

By contrast, the ABCI `Query` panic path is correctly gated behind an explicit `app.trace` debug flag via `debugErrEncoder`: [5](#0-4) [6](#0-5) 

The `DeliverTx`/giga paths above have no equivalent gate — they always emit the full stack trace regardless of the node's trace/debug configuration.

### Impact Explanation
Any client can retrieve a transaction's `raw_log`/`Log` through standard, unauthenticated public interfaces (Tendermint `tx`/`tx_search`, Cosmos REST, or Sei's EVM RPC transaction-status surfaces). If that `Log` contains a full runtime stack trace, it discloses the node's internal filesystem layout, Go module paths, and precise internal call chain of the execution engine — information intended to remain internal, aiding further targeted exploitation (analogous to GitLab's CVE-2019-6792 path-disclosure). This is a confidentiality-only leak (matches CVSS 3.1 C:L/I:N/A:N in the source CVE), not fund loss or consensus-affecting on its own, so it lands as Medium severity, consistent with the CVE score of 5.3.

### Likelihood Explanation
Reaching this code path requires only submitting a single transaction (no validator or node compromise): the comment in `makeGigaDeliverTx` documents that malformed protobuf payloads inside a submitted EVM transaction can trigger a nil-pointer/panic during execution, which is fully attacker-controlled input from an unprivileged sender.

### Recommendation
Gate the stack-trace portion of these panic-recovery `Log` messages behind the same `app.trace`/debug flag already used in `QueryResultWithDebug`/`debugErrEncoder`, so that by default only a generic message (e.g. `"panic recovered"` plus the ABCI error code) is returned to callers, with the full stack trace retained solely in server-side logs (as already done via `logger.Error(..., "stack", string(debug.Stack()))` in the same functions).

### Proof of Concept
1. Craft an EVM transaction (`MsgEVMTransaction`) with a payload designed to trigger a nil-pointer dereference during decoding/execution (per the documented "nil deref from malformed protobuf" trigger).
2. Submit it via the public EVM/Cosmos RPC to a node running with the giga executor (or legacy path) enabled.
3. Query the resulting transaction's log (`tx_search`, REST `/cosmos/tx/v1beta1/txs/{hash}`, or equivalent) and observe the `raw_log`/`Log` field contains `"recovered: <panic>\nstack:\n<full runtime stack with file paths>"` as produced by [7](#0-6)  or [3](#0-2) .

### Citations

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

**File:** app/app.go (L2126-2133)
```go
				// For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic
				logger.Error("panic in gigaDeliverTx", "panic", r, "stack", string(debug.Stack()))
				resp = abci.ResponseDeliverTx{
					Codespace: sdkerrors.UndefinedCodespace,
					Code:      sdkerrors.ErrPanic.ABCICode(),
					Log:       fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack())),
				}
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

**File:** sei-cosmos/baseapp/abci.go (L428-435)
```go
	// Add panic recovery for all queries.
	// ref: https://github.com/cosmos/cosmos-sdk/pull/8039
	defer func() {
		if r := recover(); r != nil {
			resp := sdkerrors.QueryResultWithDebug(sdkerrors.Wrapf(sdkerrors.ErrPanic, "%v", r), app.trace)
			res = &resp
		}
	}()
```

**File:** sei-cosmos/types/errors/abci.go (L101-108)
```go
// The debugErrEncoder encodes the error with a stacktrace.
func debugErrEncoder(err error) string {
	return fmt.Sprintf("%+v", err)
}

func defaultErrEncoder(err error) string {
	return err.Error()
}
```
