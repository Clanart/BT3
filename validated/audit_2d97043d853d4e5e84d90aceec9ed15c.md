### Title
Memory leak in CosmWasm FFI wrapper: `UnmanagedVector` result not destroyed on error paths — (File: sei-wasmvm/internal/api/lib.go)

### Summary
Every cgo entry point in `sei-wasmvm/internal/api/lib.go` that invokes a CosmWasm contract lifecycle call (`Instantiate`, `Execute`, `Migrate`, `Sudo`, `Reply`, `Query`, `IBCChannelOpen`, `IBCChannelConnect`, `IBCChannelClose`, `IBCPacketReceive`, `IBCPacketAck`, `IBCPacketTimeout`) receives a `C.UnmanagedVector` named `res` from the Rust side alongside an error code. On the success path the code correctly calls `copyAndDestroyUnmanagedVector(res)`, which both copies the bytes and calls `C.destroy_unmanaged_vector` to free the Rust-allocated buffer. On the error path, however, the function returns immediately with `errorWithMessage(err, errmsg)` and never calls `copyAndDestroyUnmanagedVector`/`C.destroy_unmanaged_vector` on `res` itself — only `errmsg` is destroyed (inside `errorWithMessage`). [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
This is the same bug shape as the TensorFlow `dlpack.to_dlpack` report: a resource (`res`, an `UnmanagedVector` allocated across the FFI boundary) is allocated by the callee before or during validation/execution, but when that operation later returns an error status, the caller's error-handling branch skips the destructor call for that allocation. `copyAndDestroyUnmanagedVector` is the only code path in this file that frees an `UnmanagedVector`'s underlying buffer via `C.destroy_unmanaged_vector`: [4](#0-3) 

but it is invoked exclusively on the success return of each wrapper, never on `res` in the `err != nil` branch: [5](#0-4) 

Every one of these wrappers is reached from ordinary, unprivileged CosmWasm message execution (`MsgInstantiateContract`, `MsgExecuteContract`, IBC packet handling, contract queries via gRPC/RPC, etc.), i.e., a CosmWasm user or public-RPC client can trigger the error path on demand simply by submitting a message that causes the underlying Wasm call to return a non-success errno (e.g., contract panic, out-of-gas, or any Rust-side error).

### Impact Explanation
If the Rust/libwasmvm side ever populates `res` with a non-empty allocation before signaling an error (mirroring the exact TensorFlow pattern of "allocate, then validation fails, but status isn't checked before the return"), each such call leaks that buffer's memory. Because `Execute`/`Query`/`Instantiate` are invoked once per CosmWasm message in every block, an attacker who can cheaply and repeatedly trigger a failing contract call (e.g., a wasm contract that always reverts, or malformed IBC packets) could drive unbounded memory growth on every full node and validator that processes the chain, eventually leading to an OOM crash — a node/RPC halt, which is a valid impact bucket (crash of default-configuration nodes / validator halt / block delay) under this scan's rules.

### Likelihood Explanation
Reachability is trivial and requires no privilege: any CosmWasm contract deployer/caller can force the error branch (e.g., deploy a contract whose `execute` always returns an error, or send a message that exhausts gas). The likelihood that this actually leaks memory depends on whether the Rust side (`libwasmvm`) ever writes a real allocation into `res` on the error path rather than always leaving it as `is_none`/zero-cap; that Rust-side behavior is not verifiable from the Go-side index available here, so this cannot be confirmed as an active leak — only the Go-side destructor omission (the same class of defect as the reported CVE) is concretely provable from this repository's code.

### Recommendation
Defensively call `copyAndDestroyUnmanagedVector(res)` (or at minimum `C.destroy_unmanaged_vector(res)`) in the error branch of every wrapper in `sei-wasmvm/internal/api/lib.go` before returning, exactly mirroring the pattern already used for `errmsg`, so a leak cannot occur regardless of what the Rust side deposits into `res` on failure.

### Proof of Concept
Not independently reproducible from the indexed Go-side code alone — confirming an actual leak requires inspecting the `libwasmvm` Rust implementation to determine whether `res` can be non-empty when an error errno is returned (unavailable in this index). The provable defect is the asymmetric destructor handling shown above: `res` is freed on the success path only, never on the `err != nil` path, across all listed wrapper functions in `sei-wasmvm/internal/api/lib.go`.

### Citations

**File:** sei-wasmvm/internal/api/lib.go (L195-200)
```go
	res, err := C.instantiate(cache.ptr, cs, e, i, m, db, a, q, cu64(gasLimit), cbool(printDebug), &gasReport, &errmsg)
	if err != nil && err.(syscall.Errno) != C.ErrnoValue_Success {
		// Depending on the nature of the error, `gasUsed` will either have a meaningful value, or just 0.
		return nil, convertGasReport(gasReport), errorWithMessage(err, errmsg)
	}
	return copyAndDestroyUnmanagedVector(res), convertGasReport(gasReport), nil
```

**File:** sei-wasmvm/internal/api/lib.go (L235-241)
```go
	res, err := C.execute(cache.ptr, cs, e, i, m, db, a, q, cu64(gasLimit), cbool(printDebug), &gasReport, &errmsg)
	if err != nil && err.(syscall.Errno) != C.ErrnoValue_Success {
		// Depending on the nature of the error, `gasUsed` will either have a meaningful value, or just 0.
		return nil, convertGasReport(gasReport), errorWithMessage(err, errmsg)
	}
	return copyAndDestroyUnmanagedVector(res), convertGasReport(gasReport), nil
}
```

**File:** sei-wasmvm/internal/api/lib.go (L345-351)
```go

	res, err := C.reply(cache.ptr, cs, e, r, db, a, q, cu64(gasLimit), cbool(printDebug), &gasReport, &errmsg)
	if err != nil && err.(syscall.Errno) != C.ErrnoValue_Success {
		// Depending on the nature of the error, `gasUsed` will either have a meaningful value, or just 0.
		return nil, convertGasReport(gasReport), errorWithMessage(err, errmsg)
	}
	return copyAndDestroyUnmanagedVector(res), convertGasReport(gasReport), nil
```

**File:** sei-wasmvm/internal/api/lib.go (L383-388)
```go
	res, err := C.query(cache.ptr, cs, e, m, db, a, q, cu64(gasLimit), cbool(printDebug), &gasReport, &errmsg)
	if err != nil && err.(syscall.Errno) != C.ErrnoValue_Success {
		// Depending on the nature of the error, `gasUsed` will either have a meaningful value, or just 0.
		return nil, convertGasReport(gasReport), errorWithMessage(err, errmsg)
	}
	return copyAndDestroyUnmanagedVector(res), convertGasReport(gasReport), nil
```

**File:** sei-wasmvm/internal/api/memory.go (L63-76)
```go
func copyAndDestroyUnmanagedVector(v C.UnmanagedVector) []byte {
	var out []byte
	if v.is_none {
		out = nil
	} else if v.cap == cusize(0) {
		// There is no allocation we can copy
		out = []byte{}
	} else {
		// C.GoBytes create a copy (https://stackoverflow.com/a/40950744/2013738)
		out = C.GoBytes(unsafe.Pointer(v.ptr), cint(v.len))
	}
	C.destroy_unmanaged_vector(v)
	return out
}
```
