### Title
Missing mutex protection on `VMWrapper.Query` allows concurrent CGO access to the WasmVM engine, racing with mutex-protected `Execute`/`Instantiate` calls - (File: sei-wasmd/x/wasm/keeper/vm_wrapper.go)

### Summary
`VMWrapper` in sei-wasmd wraps the `wasmvm.VM` (`types.WasmerEngine`) with a `sync.Mutex` to serialize access to the underlying CGO/Rust `libwasmvm` engine, but it only overrides `Create`, `Instantiate`, `Execute`, `Migrate`, `Sudo`, `Reply`, `Unpin`, and `Pin` — it does **not** override `Query`, so `Query` calls fall through to the embedded `types.WasmerEngine` unlocked. [1](#0-0) [2](#0-1) 

### Finding Description
The design intent of `VMWrapper` is clearly to serialize all calls into the shared wasmvm `Cache`/engine, since every state-mutating entry point (`Create`, `Instantiate`, `Execute`, `Migrate`, `Sudo`, `Reply`, `Pin`, `Unpin`) acquires `w.mu` before delegating to `w.WasmerEngine`. `Query`, however, is inherited unchanged from the embedded `types.WasmerEngine` interface field and is invoked without ever taking `w.mu`. This means a `Query` call can execute concurrently with an `Execute`/`Instantiate`/`Migrate` call against the same underlying Rust `Cache`.

The underlying FFI layer relies on `unsafe impl Send` for `GoApi` and `GoQuerier`, which wrap raw Go function pointers (`vtable`) passed across the CGO boundary without any `Sync`/thread-safety bound — an analog of the multiqueue2 pattern where `Send` is granted unconditionally to types that are not actually safe to share across threads without external synchronization: [3](#0-2) [4](#0-3) 

Because these FFI wrapper types carry no actual thread-safety guarantee themselves, the Go-side `VMWrapper` mutex is the only mechanism protecting the shared native `Cache` (instance pool, LRU cache, memory arenas) from concurrent CGO reentry. By omitting `Query` from that mutex, an unprivileged caller can trigger simultaneous native calls into the same `cache_t`/`Instance` machinery: one thread executing a state-mutating contract call (holding `w.mu`) while another thread runs `Query` unlocked, both touching shared Rust-side caches and Go-side callback state (`buildDB`, `buildAPI`, `buildQuerier`) concurrently.

### Impact Explanation
A public JSON-RPC/gRPC query (`Query`) can be issued by any client while a transaction (`Execute`/`Instantiate`) is being processed by the block executor (including the OCC parallel executor, which runs many `Execute` calls concurrently). Because `Query` bypasses the serializing mutex that guards the shared native wasmvm cache, this creates concurrent unsynchronized access to shared native memory structures. In the CGO/Rust interop context this is a genuine data-race class bug (mirroring GHSA-jphw-p3m6-pj3c's incorrect `Send` bound), which can corrupt the shared instance/module cache state or cause memory unsafety in the native library — manifesting as a crash of the node process. Since RPC/query endpoints are default-enabled and reachable by any public client, this can be used to deterministically or probabilistically cause a validator/full node crash, and because query and execution paths share the same in-process cache, corruption could also propagate to consensus-critical execution results.

### Likelihood Explanation
Likelihood is limited by the difficulty of reliably winning the race window between an in-flight `Execute`/`Instantiate` and a concurrent `Query` against the exact same CosmWasm contract's underlying compiled module/instance cache, but it requires no special privilege — any RPC client can spam `wasmd`/CosmWasm `smart query` requests against an actively-traded contract while transactions targeting the same contract are being processed, which happens routinely on a live chain under normal usage. No governance, validator, or peer compromise is needed.

### Recommendation
Add a `Query` override in `VMWrapper` that acquires `w.mu` (or a suitable read-lock if the underlying engine truly supports concurrent reads safely) before delegating to `w.WasmerEngine.Query`, so that all entry points into the native wasmvm engine are consistently synchronized:

```go
func (w *VMWrapper) Query(
    code wasmvm.Checksum,
    env wasmvmtypes.Env,
    queryMsg []byte,
    store wasmvm.KVStore,
    goapi wasmvm.GoAPI,
    querier wasmvm.Querier,
    gasMeter wasmvm.GasMeter,
    gasLimit uint64,
    deserCost wasmvmtypes.UFraction,
) ([]byte, uint64, error) {
    w.mu.Lock()
    defer w.mu.Unlock()
    return w.WasmerEngine.Query(code, env, queryMsg, store, goapi, querier, gasMeter, gasLimit, deserCost)
}
```
Additionally, audit `sei-wasmvm/libwasmvm/src/api.rs` and `querier.rs` `unsafe impl Send` usages to confirm the native cache and instance pool are actually safe for the concurrency model now permitted (or explicitly document/enforce single-writer semantics at the Go layer).

### Proof of Concept
Conceptual PoC (cannot be executed in this environment, but the code path is directly traceable):
1. Deploy a CosmWasm contract with `Instantiate`.
2. Continuously submit `Execute` transactions against the contract in one goroutine loop (simulating steady tx throughput / OCC parallel execution).
3. Concurrently issue `wasmd query wasm contract-state smart <addr> '{...}'` (or the gRPC/JSON-RPC equivalent `QuerySmartContractState`) in a tight loop from a separate client connection.
4. Because `VMWrapper.Query` (inherited, unlocked) executes concurrently with the mutex-protected `Execute` path shown at [2](#0-1)  against the same underlying `cache.ptr` / native `Instance`, race detectors (`go test -race`, or Rust-side ASAN/TSAN instrumentation on `libwasmvm`) would flag concurrent unsynchronized access, and under sustained load this is expected to eventually crash the node process.

### Citations

**File:** sei-wasmd/x/wasm/keeper/vm_wrapper.go (L15-26)
```go
type VMWrapper struct {
	types.WasmerEngine

	mu *sync.Mutex
}

func NewVMWrapper(inner types.WasmerEngine) types.WasmerEngine {
	return &VMWrapper{
		inner,
		&sync.Mutex{},
	}
}
```

**File:** sei-wasmd/x/wasm/keeper/vm_wrapper.go (L64-79)
```go
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

**File:** sei-wasmvm/libwasmvm/src/api.rs (L41-47)
```rust
// We must declare that these are safe to Send, to use in wasm.
// The known go caller passes in immutable function pointers, but this is indeed
// unsafe for possible other callers.
//
// see: https://stackoverflow.com/questions/50258359/can-a-struct-containing-a-raw-pointer-implement-send-and-be-ffi-safe
unsafe impl Send for GoApi {}

```

**File:** sei-wasmvm/libwasmvm/src/querier.rs (L35-37)
```rust
// TODO: check if we can do this safer...
unsafe impl Send for GoQuerier {}

```
