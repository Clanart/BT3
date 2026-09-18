### Title
`VMWrapper.Create` timeout abandons the underlying WasmVM compile goroutine, allowing unsynchronized concurrent access to the Rust `libwasmvm` module cache from `MsgStoreCode` - (File: `sei-wasmd/x/wasm/keeper/vm_wrapper.go`)

### Summary
`VMWrapper.Create`, invoked on every `MsgStoreCode` upload, launches the actual `WasmerEngine.Create` (`libwasmvm` `save_wasm`) call in a background goroutine guarded by a 15s timeout. If the compile takes longer than `CreateTimeout`, the function returns a timeout error to the caller and releases its `sync.Mutex`, but it never cancels or waits for the spawned goroutine, which keeps running the cgo call into the Wasm cache. This mirrors the tc358743 bug class: a background worker (there, a polling timer; here, a compile goroutine) started before a possible early/timeout return is not torn down on that return path, so it keeps touching shared state after the guarding lock has already been released.

### Finding Description
`(w *VMWrapper) Create` takes `w.mu.Lock()`, starts `go func() { checksum, err = w.WasmerEngine.Create(code); done <- struct{}{} }()`, and races that against a 15-second `time.Timer`. On timeout it returns `errors.New("create wasm code timed out")` and `defer w.mu.Unlock()` fires, but the goroutine calling into the underlying `WasmerEngine.Create` (which reaches `libwasmvm`'s `save_wasm`/`do_save_wasm` cgo call operating on the shared `cache_t*`) is left running unbounded. [1](#0-0) 

Because `w.mu` is the only serialization mechanism protecting calls into the single shared Wasm VM cache (`Create`, `Instantiate`, `Execute` all lock the same mutex before calling into `w.WasmerEngine`), unlocking it while the previous `Create` goroutine is still executing inside the same underlying engine breaks the mutual-exclusion invariant the wrapper is trying to enforce. [2](#0-1) 

The underlying engine's `Create`/`StoreCode` calls into `api.StoreCode(vm.cache, code)` → `C.save_wasm(cache.ptr, ...)`, a single shared `*cache_t` pointer protected on the Rust side by its own internal locking for the specific operation, but concurrent `save_wasm` invocations racing with newly-admitted `Create`/`Instantiate`/`Execute` calls that assume `w.mu` gives exclusive access is a violation of the intended concurrency contract at the Go wrapper layer. [3](#0-2) [4](#0-3) 

`create` in the wasm keeper is reached directly from any user-submitted `MsgStoreCode` (or equivalent CosmWasm code-upload path), calling `k.getWasmer(ctx).Create(wasmCode)` on attacker-controlled bytecode. [5](#0-4) 

### Impact Explanation
An attacker who is any CosmWasm user can craft or select Wasm bytecode whose compilation legitimately or artificially exceeds 15 seconds (e.g., very large, deeply nested, or pathological modules that are still within `MaxWasmSize`/gas limits but slow to compile in the Cranelift/Wasmer backend). Each such upload:
- Causes `Create` to return a timeout error to the keeper (upload is rejected/charged gas), but
- Leaves an orphaned goroutine executing the cgo compile call against the shared cache pointer with no cancellation.

Repeated triggering (each `MsgStoreCode` with pathological bytecode spawns another abandoned goroutine) accumulates concurrent, unsynchronized calls into the same underlying Rust cache object from multiple threads via cgo, alongside subsequent legitimately-locked `Instantiate`/`Execute`/`Create` calls that believe they hold exclusive access. This can corrupt the module cache's internal state or race on shared native (non-Go-GC) memory, which for cgo/Rust FFI boundaries is undefined behavior and can crash the node process — a crash of the validator/full node consensus-critical process, not just RPC, satisfies the "crash of default-configuration node" / "block delay" impact bar, since a validator whose process aborts stops producing/validating blocks and can contribute to a broader halt if enough validators are affected by the same repeatable trigger. It is at minimum a validator/full-node instability and process-crash vector triggerable purely from an ordinary `MsgStoreCode` transaction, with no special privileges required.

### Likelihood Explanation
Likelihood is moderate: the attacker needs to author or find a Wasm module that reliably compiles in >15 seconds without exceeding `MaxWasmSize`/gas metering already charged for compilation (`CompileCosts`), which is plausible given cgo/Rust compilers can have superlinear compile time on certain constructs, and this is entirely within reach of any funded account able to submit `MsgStoreCode`. No special CosmWasm authorization beyond normal code-upload permission is needed (subject to chain params allowing code upload, which is typically permissionless or governance-set to `Everybody`).

### Recommendation
Make `VMWrapper.Create` actually bound the goroutine's lifetime and its effect on shared state:
- Either drop the artificial timeout and let `Create` block until the real, gas-metered compile completes (gas already charges for compile cost, so the timeout is redundant protection against an already-bounded operation), or
- If a timeout is kept, ensure the mutex is held until the background goroutine has actually finished (do not release `w.mu` early), and/or use a mechanism to signal/cancel the underlying compile (if `libwasmvm` supports cancellation) so no orphaned call into the shared cache pointer can race with subsequent locked operations.
- At minimum, do not release `w.mu` on the timeout branch until the spawned goroutine's `done` signal is received (i.e., wait for it, even though the timeout error is returned to the caller), preserving the wrapper's mutual-exclusion guarantee over the shared VM cache.

### Proof of Concept
1. Craft (or reuse a known pathological) Wasm module `slow.wasm` that is valid, passes static analysis, is within `MaxWasmSize`, but takes the `libwasmvm` compiler noticeably longer than 15 seconds to `save_wasm`/compile (achievable with deeply nested control flow or huge function bodies at the size limit).
2. Submit `MsgStoreCode` with `slow.wasm` from any account with upload permission. `Keeper.create` calls `k.getWasmer(ctx).Create(wasmCode)` → `VMWrapper.Create`.
3. `VMWrapper.Create` spawns the compile goroutine, waits 15s, times out, returns an error to `create`, and releases `w.mu` — while the compile goroutine is still running inside `libwasmvm`'s `save_wasm` against the shared `cache.ptr`.
4. Immediately submit another transaction using the wasm VM (`MsgInstantiateContract`/`MsgExecuteContract`/another `MsgStoreCode`) which acquires `w.mu` and calls into the same underlying `WasmerEngine`/cache while the first goroutine's cgo call is still in flight, producing concurrent unsynchronized native calls on the shared cache pointer.
5. Repeat under load; observe unsynchronized access/corruption of the Wasm module cache and potential process crash of the node executing these transactions.

### Citations

**File:** sei-wasmd/x/wasm/keeper/vm_wrapper.go (L28-45)
```go
func (w *VMWrapper) Create(code wasmvm.WasmCode) (checksum wasmvm.Checksum, err error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	timer := time.NewTimer(CreateTimeout)
	done := make(chan struct{}, 1)
	go func() {
		checksum, err = w.WasmerEngine.Create(code)
		done <- struct{}{}
	}()
	select {
	case <-done:
		timer.Stop()
		return
	case <-timer.C:
		err = errors.New("create wasm code timed out")
		return
	}
}
```

**File:** sei-wasmd/x/wasm/keeper/vm_wrapper.go (L47-79)
```go
func (w *VMWrapper) Instantiate(
	checksum wasmvm.Checksum,
	env wasmvmtypes.Env,
	info wasmvmtypes.MessageInfo,
	initMsg []byte,
	store wasmvm.KVStore,
	goapi wasmvm.GoAPI,
	querier wasmvm.Querier,
	gasMeter wasmvm.GasMeter,
	gasLimit uint64,
	deserCost wasmvmtypes.UFraction,
) (*wasmvmtypes.Response, uint64, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.WasmerEngine.Instantiate(checksum, env, info, initMsg, store, goapi, querier, gasMeter, gasLimit, deserCost)
}

func (w *VMWrapper) Execute(
	code wasmvm.Checksum,
	env wasmvmtypes.Env,
	info wasmvmtypes.MessageInfo,
	executeMsg []byte,
	store wasmvm.KVStore,
	goapi wasmvm.GoAPI,
	querier wasmvm.Querier,
	gasMeter wasmvm.GasMeter,
	gasLimit uint64,
	deserCost wasmvmtypes.UFraction,
) (*wasmvmtypes.Response, uint64, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.WasmerEngine.Execute(code, env, info, executeMsg, store, goapi, querier, gasMeter, gasLimit, deserCost)
}
```

**File:** sei-wasmvm/internal/api/lib.go (L61-70)
```go
func StoreCode(cache Cache, wasm []byte) ([]byte, error) {
	w := makeView(wasm)
	defer runtime.KeepAlive(wasm)
	errmsg := uninitializedUnmanagedVector()
	checksum, err := C.save_wasm(cache.ptr, w, cbool(false), &errmsg)
	if err != nil {
		return nil, errorWithMessage(err, errmsg)
	}
	return copyAndDestroyUnmanagedVector(checksum), nil
}
```

**File:** sei-wasmvm/lib.go (L44-47)
```go
// Deprecated: Renamed to StoreCode
func (vm *VM) Create(code WasmCode) (Checksum, error) {
	return vm.StoreCode(code)
}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L209-239)
```go
func (k Keeper) create(ctx sdk.Context, creator sdk.AccAddress, wasmCode []byte, instantiateAccess *types.AccessConfig, authZ AuthorizationPolicy) (codeID uint64, err error) {
	if creator == nil {
		return 0, sdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "cannot be nil")
	}

	if !authZ.CanCreateCode(k.getUploadAccessConfig(ctx), creator) {
		return 0, sdkerrors.Wrap(sdkerrors.ErrUnauthorized, "can not create code")
	}
	// figure out proper instantiate access
	defaultAccessConfig := k.getInstantiateAccessConfig(ctx).With(creator)
	if instantiateAccess == nil {
		instantiateAccess = &defaultAccessConfig
	} else if !instantiateAccess.IsSubset(defaultAccessConfig) {
		// we enforce this must be subset of default upload access
		return 0, sdkerrors.Wrap(sdkerrors.ErrUnauthorized, "instantiate access must be subset of default upload access")
	}

	if types.MaxWasmSize < 0 {
		return 0, sdkerrors.Wrap(types.ErrCreateFailed, "max wasm size is negative")
	}
	// #nosec G115 -- MaxWasmSize is checked above to be non-negative
	wasmCode, err = ioutils.Uncompress(wasmCode, uint64(types.MaxWasmSize))
	if err != nil {
		return 0, sdkerrors.Wrap(types.ErrCreateFailed, err.Error())
	}
	ctx.GasMeter().ConsumeGas(k.gasRegister.CompileCosts(len(wasmCode)), "Compiling WASM Bytecode")

	checksum, err := k.getWasmer(ctx).Create(wasmCode)
	if err != nil {
		return 0, sdkerrors.Wrap(types.ErrCreateFailed, err.Error())
	}
```
