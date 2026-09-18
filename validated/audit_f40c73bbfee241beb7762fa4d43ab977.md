Based on my investigation, I found a genuine analog: the `giga/evmonly` EVM-only Block-STM/OCC executor's parallel-execution path has no per-task panic recovery, unlike every other transaction-execution path in this codebase (`app/app.go`'s `ProcessTxsSynchronousGiga`, `makeGigaDeliverTx`, `ProcessBlock`, `legacyabci.DeliverTx`, `baseapp.runTx` all wrap per-tx execution in `recover()`).

### Title
Unrecovered panic in giga OCC-executor worker goroutine crashes the node process instead of being converted into a per-tx error - ([File: giga/evmonly/occ.go])

### Summary
The `evmonly.Executor`'s optimistic-concurrency (Block-STM style) execution path dispatches per-transaction work into worker goroutines via `occWorkerPool.Run`, which uses `golang.org/x/sync/errgroup`. Neither the worker closures nor `executeTaskInto`/`executeTx`/`executeTxSpeculative` install a `recover()`. Every other transaction-execution surface in the same repository (`app.ProcessTxsSynchronousGiga`, `app.makeGigaDeliverTx`, `app.ProcessBlock`, `legacyabci.DeliverTx`, `baseapp.runTx`) explicitly wraps single-transaction execution in an IIFE with `defer recover()` specifically so a panicking transaction cannot escape and take down more than its own draft/tx. The `evmonly` OCC path lacks this pattern entirely.

### Finding Description
`executeBlockOCC` builds a speculative runner and calls `runner.runRanges`, which calls `pool.Run(...)`, spawning one `errgroup.Go` goroutine per worker: [1](#0-0) 
Inside each worker, `executeTaskInto` invokes `executeTx` → `executor.executeTxSpeculative`, none of which have panic recovery: [2](#0-1) [3](#0-2) 
`executeTxSpeculative` runs an actual EVM interpreter (`vm.NewEVM(...).../core.ApplyMessage`-equivalent path) against attacker-controlled transaction bytes (arbitrary bytecode/calldata), the same class of code that the analogous Cosmos-side paths defensively wrap in `recover()` because EVM execution is known to be able to panic (nil dereferences from malformed data, index out-of-range in tracing/bytecode handling, stack overflow in deeply recursive calls, etc.) — see the explicit comments and tests acknowledging this in the Cosmos-giga path: [4](#0-3) [5](#0-4) 
In Go, an unrecovered panic inside a goroutine spawned by `errgroup.Go` is not contained by the calling goroutine's error return — it propagates and crashes the entire process, since panics only unwind within the goroutine that panicked and are fatal if unhandled. There is no top-level `recover()` in `ExecuteBlock`/`ExecutePreparedBlock`/`executeBlockOCC`/`validateBlockSTM` that would catch a panic surfacing from inside these worker goroutines. This is a materially worse variant of the IoTeX bug: instead of merely failing to evict a bad sender's transaction from the pool (causing a repeatedly-failing draft), a single malicious/malformed EVM transaction can crash the entire node process running the giga-evmonly executor whenever OCC (parallel) execution is selected for a block.

### Impact Explanation
A crash of the executing node is a denial-of-service against any validator/full node that uses the `giga/evmonly` OCC-parallel executor for block execution. Because block execution must be deterministic across all nodes to maintain consensus, if every node processes the same block, an attacker who can craft one transaction that panics inside `executeTxSpeculative` (e.g., via edge-case EVM/precompile behavior, nil-pointer conditions in state-DB helpers, or index-out-of-range paths reachable through crafted calldata) can crash every validator running this code path simultaneously, causing a chain halt — a "crash of default-configuration RPC/validator nodes" per the rules, and potentially a validator-halt scenario if OCC-mode giga-evmonly execution is enabled in production.

### Likelihood Explanation
Reachability requires only submitting an ordinary EVM transaction (fully within an unprivileged sender's capability) that triggers a panic somewhere in the speculative EVM execution path (`executeTx`/`ApplyMessage`/`vm.EVM`/`nativeStateDB` helpers) when OCC mode is engaged (multiple txs in a block, `OCCWorkers > 1`, no registered custom precompiles disqualifying OCC). Given `evmonly` is described in its own README as still under active development ("Custom precompiles are still placeholders... open work is to port them"), the probability of an unhandled edge case in this comparatively new, less-hardened code path causing a panic is non-trivial, especially compared to the already-defensive (recover-wrapped) legacy Cosmos-giga and V2 paths.

### Recommendation
Wrap each unit of speculative-execution work with panic recovery at the goroutine boundary, converting a panic into an `error` returned from the worker closure (mirroring the pattern already used in `app/app.go`'s `ProcessTxsSynchronousGiga`/`makeGigaDeliverTx` and `sei-tendermint`'s `mempool/reactor` `handleMessage`). Concretely, add a `defer recover()` inside `occWorkerPool.Run`'s per-worker closure (or inside `executeTaskInto`) that converts any panic into a normal `error`, which `executeBlockOCC` can then route to `executeBlockOCCSequentialFallback` (mirroring the existing max-incarnation/pool-closed fallback logic) so a single bad transaction degrades gracefully to the sequential path (and ultimately to an ordinary failed-tx receipt) instead of crashing the process.

### Proof of Concept
Not directly reproducible from static analysis alone: exploitation requires finding a concrete transaction payload that panics inside `executeTxSpeculative`'s EVM execution (e.g., a specific malformed calldata/precompile interaction triggering a nil dereference or an out-of-bounds access in `nativeStateDB` or go-ethereum's `vm` package under the `evmonly` fail-fast custom-precompile configuration). I was unable to identify or verify a specific such payload from the indexed code alone; a background engineer with fuzzing/debugging tools would need to fuzz `executeTxSpeculative` inputs (or audit `nativeStateDB` snapshot/undo logic and the custom fail-fast precompiles) to construct a concrete crashing transaction and confirm process-level termination when `OCCWorkers > 1`.

### Citations

**File:** giga/evmonly/occ_pool.go (L24-48)
```go
func (p *occWorkerPool) Run(ctx context.Context, workItems int, run func(context.Context, int, int) error) error {
	workers := p.workers
	if workItems > 0 {
		workers = min(workers, workItems)
	}
	p.mu.RLock()
	if p.closed {
		p.mu.RUnlock()
		return errOCCWorkerPoolClosed
	}
	p.wg.Add(1)
	p.mu.RUnlock()
	defer p.wg.Done()

	g, groupCtx := errgroup.WithContext(ctx)
	for workerID := 0; workerID < workers; workerID++ {
		workerID := workerID
		g.Go(func() error {
			if err := groupCtx.Err(); err != nil {
				return err
			}
			return run(groupCtx, workerID, workers)
		})
	}
	return g.Wait()
```

**File:** giga/evmonly/occ.go (L185-197)
```go
func (r occSpeculativeRunner) executeTaskInto(ctx context.Context, task occExecutionTask, results []occTxExecution) error {
	result, err := r.executeTx(ctx, task.source, task.txIndex, task.txIndexUint, task.gasLimit)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return ctxErr
		}
		result.err = err
	}
	result.incarnation = task.incarnation
	result.sourcePrefix = task.sourcePrefix
	results[task.txIndex] = result
	return nil
}
```

**File:** giga/evmonly/occ.go (L222-268)
```go
func (e *Executor) executeTxSpeculative(
	ctx context.Context,
	source StateReader,
	req PreparedBlock,
	txIndex int,
	txIndexUint uint,
	chainConfig *params.ChainConfig,
	blockCtx vm.BlockContext,
	baseFee *big.Int,
	gasLimit uint64,
) (occTxExecution, error) {
	if err := ctx.Err(); err != nil {
		return occTxExecution{}, err
	}
	p := req.Txs[txIndex]
	stateDB := e.acquireStateDB(source)
	defer e.releaseStateDB(stateDB)
	stateDB.enableAccessTracking()
	evm := vm.NewEVM(blockCtx, stateDB, chainConfig, vm.Config{}, nil)
	stateDB.SetEVM(evm)
	gasPool := new(core.GasPool).AddGas(gasLimit)
	txResult, receipt, err := e.executeTx(
		evm,
		stateDB,
		gasPool,
		req.Context,
		p,
		txIndex,
		txIndexUint,
		baseFee,
	)
	readSet, writeSet := stateDB.accessSets()
	result := occTxExecution{
		txResult:                 txResult,
		receipt:                  receipt,
		readSet:                  readSet,
		writeSet:                 writeSet,
		gasUsed:                  txResult.GasUsed,
		gasLimit:                 p.Tx.Gas(),
		commutativeBalanceDeltas: stateDB.commutativeBalanceDeltasBig(),
	}
	if err != nil {
		return result, fmt.Errorf("execute tx %d %s: %w", txIndex, p.Tx.Hash(), err)
	}
	stateDB.ChangeSetInto(&result.changeSet)
	return result, nil
}
```

**File:** app/app.go (L1427-1460)
```go
		// Execute EVM transaction through giga executor with panic recovery.
		// Matches V2's recover behavior in legacyabci/deliver_tx.go.
		var result *abci.ExecTxResult
		var execErr error
		// fallbackToV2: store-iteration panic; re-run this tx via v2 to match v2.
		var fallbackToV2 bool
		// IIFE (immediately-invoked function) to scope defer/recover to this tx only,
		// allowing the loop to continue processing subsequent transactions after a panic.
		func() {
			defer func() {
				if r := recover(); r != nil {
					// Handle panics by type (matches V2's recovery middleware in baseapp/recovery.go)
					if oogErr, isOOG := r.(sdk.ErrorOutOfGas); isOOG {
						result = &abci.ExecTxResult{
							Codespace: sdkerrors.RootCodespace,
							Code:      sdkerrors.ErrOutOfGas.ABCICode(),
							Log:       fmt.Sprintf("out of gas in location: %v", oogErr.Descriptor),
						}
						return
					}
					// Store-iteration panic: giga can't handle this tx; fall back to v2 (mirrors makeGigaDeliverTx).
					if err, ok := r.(error); ok && errors.Is(err, gigastore.ErrIteratorUnsupported) {
						fallbackToV2 = true
						return
					}
					// For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic
					logger.Error("panic in giga synchronous executor", "panic", r, "stack", string(debug.Stack()))
					result = &abci.ExecTxResult{
						Codespace: sdkerrors.UndefinedCodespace,
						Code:      sdkerrors.ErrPanic.ABCICode(),
						Log:       fmt.Sprintf("panic recovered: %v", r),
					}
				}
			}()
```

**File:** giga/tests/giga_test.go (L2092-2113)
```go
// TestGigaOCC_PanicRecovery verifies that the Giga OCC executor handles errors gracefully.
// This tests the panic recovery mechanism by running multiple transactions through OCC mode.
func TestGigaOCC_PanicRecovery(t *testing.T) {
	blockTime := time.Now()
	accts := utils.NewTestAccounts(5)
	workers := 4
	txCount := 10

	transfers := GenerateNonConflictingTransfers(txCount)

	gigaOCCCtx := NewGigaTestContext(t, accts, blockTime, workers, ModeGigaOCC)
	gigaOCCTxs := CreateEVMTransferTxs(t, gigaOCCCtx, transfers, true)
	_, gigaOCCResults, err := RunBlock(t, gigaOCCCtx, gigaOCCTxs)

	// The key assertion: the test completes without crashing (panic recovery working)
	require.NoError(t, err, "Giga OCC should not return error")
	require.Len(t, gigaOCCResults, txCount, "Should have results for all transactions")

	for i, result := range gigaOCCResults {
		require.Equal(t, uint32(0), result.Code, "tx[%d] should succeed, got code=%d log=%s", i, result.Code, result.Log)
	}
}
```
