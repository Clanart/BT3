### Title
`eth_estimateGas` / `eth_createAccessList` lack the wall-clock timeout applied to `eth_call` and `eth_estimateGasAfterCalls`, allowing an attacker-deployed contract to hang RPC-serving goroutines indefinitely - ([File: evmrpc/simulate.go])

### Summary
`SimulationAPI.Call` and `SimulationAPI.EstimateGasAfterCalls` both forward `s.backend.RPCEVMTimeout()` into `export.DoCall` / `export.DoEstimateGasAfterCalls`, bounding execution wall-clock time via `context.WithTimeout` + `evm.Cancel()`. `SimulationAPI.EstimateGas` and `SimulationAPI.CreateAccessList`, however, call `export.DoEstimateGas` / `export.AccessList` without ever threading a timeout into the context, so the only bound on these calls is gas (`RPCGasCap`) and a concurrency semaphore — not wall-clock time. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`eth_estimateGas`'s implementation performs a binary search over gas values, executing `DoCall`-equivalent EVM (and, when the target is the `wasmd` precompile, CosmWasm) execution repeatedly per RPC request. `EstimateGas` is a public-RPC entrypoint reachable by any unprivileged caller: it accepts arbitrary `to`/`input` from `export.TransactionArgs`, and `ctx = context.WithValue(ctx, CtxIsWasmdPrecompileCallKey, wasmd.IsWasmdCall(args.To))` shows the code path explicitly anticipates calls into the `wasmd` precompile (i.e., attacker-deployed/attacker-invoked CosmWasm contracts) going through this RPC method. [4](#0-3) 

Unlike `Call`/`EstimateGasAfterCalls`, which pass `s.backend.RPCEVMTimeout()` so that a background goroutine can call `evm.Cancel()` once the deadline fires (the same enforcement pattern used elsewhere in `evmrpc/block_trace_profiled.go` for `debug_traceCall`), `EstimateGas` supplies no timeout at all to `DoEstimateGas`. The only defenses present are `RPCGasCap` (bounds *gas*, not real time) and the `requestLimiter` semaphore (bounds *concurrency*, not per-call duration). Because CosmWasm execution invoked through the `wasmd` precompile (`precompiles/wasmd/wasmd.go` `query`/`execute`) runs via cgo into the Wasm VM, and CW gas metering does not necessarily track 1:1 with the EVM's own opcode-gas-to-CPU-time ratio, an attacker can craft a contract/query whose gas cost (up to `RPCGasCap`) is cheap in gas terms but expensive in real CPU time (e.g., heavy cryptographic loops, large storage iteration, or expensive JSON/message deserialization metered lightly relative to actual CPU work). Each of `DoEstimateGas`'s O(log(gasCap)) binary-search iterations re-executes this expensive path, and with no deadline in the context, nothing interrupts a slow iteration.

This mirrors the CVE-2021-22100 bug class precisely: a caller (analogous to the "service broker" developer) can push a component (a deployed contract/CosmWasm code) that, when invoked through a specific serving path lacking a timeout, causes the serving instance (the RPC node's request-handling goroutine) to hang, and because concurrent requests each grab (or fail to grab) a semaphore slot but never get preempted by a deadline, a burst of such calls can pin CPU and goroutines for an extended, effectively unbounded period, degrading or denying `eth_estimateGas`/`eth_createAccessList` service for legitimate users on any default-configuration public RPC node. [5](#0-4) 

### Impact Explanation
This affects only the public EVM JSON-RPC surface (`eth_estimateGas`, `eth_createAccessList`), not consensus-critical state transition, so it cannot cause fund loss, inflation, or a chain split. However, it can cause sustained excessive CPU/goroutine consumption on default-configuration RPC nodes serving these methods, denying service to legitimate `eth_estimateGas`/`eth_createAccessList` callers (wallets, dApps, tooling that depend on gas estimation before submitting transactions) — a crash/DoS of default-configuration RPC nodes as scoped by the validation criteria.

### Likelihood Explanation
Likelihood is limited by uncertainty over whether real, gas-cheap-but-CPU-expensive operations reachable via the `wasmd` precompile (or plain EVM opcodes) exist in practice with a large enough gas/CPU-time skew to matter, and by the presence of `RPCGasCap` capping total gas per iteration and the `requestLimiter` capping concurrency. Exploitability would require empirical confirmation that a specific message/opcode sequence produces a meaningfully disproportionate CPU cost relative to its gas cost under `RPCGasCap`; I could not verify this quantitatively from static analysis alone.

### Recommendation
Thread `s.backend.RPCEVMTimeout()` (or an equivalent explicit deadline) into `export.DoEstimateGas` and `export.AccessList` the same way it is already applied in `Call` and `EstimateGasAfterCalls`, and ensure any downstream CosmWasm/cgo call path used by wasmd-precompile queries respects context cancellation (or is otherwise capped by a hard wall-clock watchdog) rather than relying solely on gas metering.

### Proof of Concept
Not independently reproduced. Based on static analysis: deploy a contract exposing a wasmd-precompile call (or EVM code) whose per-unit-gas CPU cost is high, call it repeatedly via `eth_estimateGas` at/near `RPCGasCap`, and observe (a) individual `eth_estimateGas` calls taking far longer than the `RPCEVMTimeout` that would bound an equivalent `eth_call`, and (b) concurrent legitimate `eth_estimateGas`/`eth_createAccessList` requests degrading in latency without any deadline-based recovery. Confirming actual exploitability requires running this against a live node, which I could not do from the codebase alone.

### Citations

**File:** evmrpc/simulate.go (L51-55)
```go
type SimulationAPI struct {
	backend        *Backend
	connectionType ConnectionType
	requestLimiter *semaphore.Weighted
}
```

**File:** evmrpc/simulate.go (L116-139)
```go
func (s *SimulationAPI) EstimateGas(ctx context.Context, args export.TransactionArgs, blockNrOrHash *rpc.BlockNumberOrHash, overrides *export.StateOverride) (result hexutil.Uint64, returnErr error) {
	startTime := time.Now()
	defer func() {
		recordMetricsWithError(ctx, "eth_estimateGas", s.connectionType, startTime, returnErr, recover())
	}()
	if returnErr = validateStateOverrides(overrides, s.backend.MaxStateOverrideAccounts(), s.backend.MaxStateOverrideSlots()); returnErr != nil {
		return
	}
	/* ---------- fail‑fast limiter ---------- */
	if s.requestLimiter != nil {
		if !s.requestLimiter.TryAcquire(1) {
			returnErr = errors.New("eth_estimateGas rejected due to rate limit: server busy")
			return
		}
		defer s.requestLimiter.Release(1)
	}
	bNrOrHash := rpc.BlockNumberOrHashWithNumber(rpc.LatestBlockNumber)
	if blockNrOrHash != nil {
		bNrOrHash = *blockNrOrHash
	}
	ctx = context.WithValue(ctx, CtxIsWasmdPrecompileCallKey, wasmd.IsWasmdCall(args.To))
	estimate, err := export.DoEstimateGas(ctx, s.backend, args, bNrOrHash, overrides, nil, s.backend.RPCGasCap())
	return estimate, err
}
```

**File:** evmrpc/simulate.go (L141-169)
```go
func (s *SimulationAPI) EstimateGasAfterCalls(ctx context.Context, args export.TransactionArgs, calls []export.TransactionArgs, blockNrOrHash *rpc.BlockNumberOrHash, overrides *export.StateOverride) (result hexutil.Uint64, returnErr error) {
	startTime := time.Now()
	defer func() {
		recordMetricsWithError(ctx, "eth_estimateGasAfterCalls", s.connectionType, startTime, returnErr, recover())
	}()
	// Reject over-sized requests early, before any state wrapping or resource acquisition.
	if maxCalls := s.backend.MaxEstimateGasCalls(); maxCalls > 0 && len(calls) > maxCalls {
		returnErr = fmt.Errorf("eth_estimateGasAfterCalls: too many calls (%d > %d)", len(calls), maxCalls)
		return
	}
	if returnErr = validateStateOverrides(overrides, s.backend.MaxStateOverrideAccounts(), s.backend.MaxStateOverrideSlots()); returnErr != nil {
		return
	}
	/* ---------- fail‑fast limiter ---------- */
	if s.requestLimiter != nil {
		if !s.requestLimiter.TryAcquire(1) {
			returnErr = errors.New("eth_estimateGasAfterCalls rejected due to rate limit: server busy")
			return
		}
		defer s.requestLimiter.Release(1)
	}
	bNrOrHash := rpc.BlockNumberOrHashWithNumber(rpc.LatestBlockNumber)
	if blockNrOrHash != nil {
		bNrOrHash = *blockNrOrHash
	}
	ctx = context.WithValue(ctx, CtxIsWasmdPrecompileCallKey, wasmd.IsWasmdCall(args.To))
	estimate, err := export.DoEstimateGasAfterCalls(ctx, s.backend, args, calls, bNrOrHash, overrides, s.backend.RPCEVMTimeout(), s.backend.RPCGasCap())
	return estimate, err
}
```

**File:** evmrpc/simulate.go (L171-210)
```go
func (s *SimulationAPI) Call(ctx context.Context, args export.TransactionArgs, blockNrOrHash *rpc.BlockNumberOrHash, overrides *export.StateOverride, blockOverrides *export.BlockOverrides) (result hexutil.Bytes, returnErr error) {
	startTime := time.Now()
	defer func() {
		recordMetricsWithError(ctx, "eth_call", s.connectionType, startTime, returnErr, recover())
	}()
	if returnErr = validateStateOverrides(overrides, s.backend.MaxStateOverrideAccounts(), s.backend.MaxStateOverrideSlots()); returnErr != nil {
		return
	}
	/* ---------- fail‑fast limiter ---------- */
	if s.requestLimiter != nil {
		if !s.requestLimiter.TryAcquire(1) {
			returnErr = errors.New("eth_call rejected due to rate limit: server busy")
			return
		}
		defer s.requestLimiter.Release(1)
	}
	defer func() {
		if r := recover(); r != nil {
			if strings.Contains(fmt.Sprintf("%s", r), "Int overflow") {
				returnErr = errors.New("error: balance override overflow")
			} else {
				returnErr = fmt.Errorf("something went wrong: %v", r)
			}
		}
	}()
	if blockNrOrHash == nil {
		latest := rpc.BlockNumberOrHashWithNumber(rpc.LatestBlockNumber)
		blockNrOrHash = &latest
	}
	ctx = context.WithValue(ctx, CtxIsWasmdPrecompileCallKey, wasmd.IsWasmdCall(args.To))
	callResult, err := export.DoCall(ctx, s.backend, args, *blockNrOrHash, overrides, blockOverrides, s.backend.RPCEVMTimeout(), s.backend.RPCGasCap())
	if err != nil {
		return nil, err
	}
	// If the result contains a revert reason, try to unpack and return it.
	if len(callResult.Revert()) > 0 {
		return nil, NewRevertError(callResult)
	}
	return callResult.Return(), callResult.Err
}
```
