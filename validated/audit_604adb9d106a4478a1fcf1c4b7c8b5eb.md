### Title
Unbounded Predecessor Iteration in `TraceTx` gRPC Endpoint Bypasses Timeout and Enables Node Resource Exhaustion — (File: `x/evm/keeper/grpc_query.go`)

### Summary
The `TraceTx` gRPC endpoint iterates over an unbounded, caller-supplied list of predecessor transactions (`req.Predecessors`) with no upper-bound guard and no timeout coverage. Each predecessor is fully executed via `ApplyMessageWithConfig(..., commit=true)`. Because the predecessor loop executes inside the `msgCb` callback — which is called *before* `prepareTrace` installs the per-trace deadline — the 5-second `defaultTraceTimeout` does not protect it. An unprivileged caller can submit a single `TraceTx` request carrying thousands of complex predecessors, pinning the node's CPU and memory for an unbounded duration.

### Finding Description

In `x/evm/keeper/grpc_query.go`, `TraceTx` passes a closure to `execTrace` that iterates over `req.Predecessors` without any length check:

```go
// grpc_query.go  lines 542-563
for i, tx := range req.Predecessors {          // ← no len() guard
    ethTx := tx.AsTransaction()
    msg, err := core.TransactionToMessage(ethTx, signer, cfg.BaseFee)
    if err != nil { ... continue }
    ...
    rsp, err := k.ApplyMessageWithConfig(ctx, msg, cfg, true)  // commit=true
    if err != nil { ... continue }
    cfg.TxConfig.LogIndex += uint(len(rsp.Logs))
}
```

`execTrace` calls `msgCb` (the closure above) **before** it calls `prepareTrace`, which is where the `context.WithTimeout` deadline is installed:

```go
// grpc_query.go  lines 498-503
msg, err := msgCb(ctx, cfg, req.GetTraceConfig())   // predecessor loop runs here — no timeout
...
result, _, err := k.prepareTrace(ctx, cfg, msg, ...)  // timeout only starts here
```

`prepareTrace` itself sets up the deadline at line 788:

```go
deadlineCtx, cancel := context.WithTimeout(ctx.Context(), timeout)
```

Because the predecessor loop completes entirely before `prepareTrace` is entered, the `defaultTraceTimeout = 5 * time.Second` guard is irrelevant to the predecessor phase. Each predecessor call to `ApplyMessageWithConfig` with `commit=true` performs a full EVM state transition (including `stateDB.Commit()`) against the branched query context, consuming real CPU and memory proportional to the predecessor's gas limit.

By contrast, `SimulateV1` — the only other multi-call gRPC path — does enforce a bound:

```go
// grpc_query.go  lines 870-871
if len(payload.Opts.BlockStateCalls) > rpctypes.MaxSimulateBlocks {
    return nil, status.Error(codes.InvalidArgument, "too many blocks in blockStateCalls")
}
```

No equivalent guard exists for `req.Predecessors` in `TraceTx`.

`TraceBlock` has the same structural problem — it iterates over `req.Txs` without a length cap — but each iteration does enter `prepareTrace` and therefore is subject to the per-tx timeout, making