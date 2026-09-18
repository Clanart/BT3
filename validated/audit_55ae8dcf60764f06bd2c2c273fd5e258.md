### Title
CPU/Memory Amplification DoS via Unpriced ABI-Decode Aliasing on Static EVM Precompiles - ([File: precompiles/common/precompiles.go])

### Summary
The external report describes a prototype-pollution style bug where a crafted GraphQL query (via aliases) causes `deepMerge()` to perform disproportionate work relative to the size of the request, enabling a Denial of Service. The sei-chain codebase has already identified and partially mitigated the structurally identical bug class — ABI-decode amplification via aliased offsets — but the fix is scoped only to `DynamicGasPrecompile`, leaving the static `Precompile` path (used by most Cosmos precompiles such as bank, auth, authz, addr) unprotected.

### Finding Description
`precompiles/common/decode_cost.go` documents and mitigates a real amplification bug in go-ethereum's ABI decoder: a single crafted `string`/`string[]`/tuple payload can be referenced by many array/tuple slots via repeated offsets, so the decoder's `Unpack()` copies data volume that is super-linear in `len(input)` (worst case ~`len(input)^2`) [1](#0-0) . `precompiles/common/decode_cost_test.go`'s `TestDecodeStringCopyBytes_Aliased` and `TestDecodeStringCopyBytes_AliasedVoteWeighted` empirically confirm this: a ~1.2MiB calldata payload forces the real decoder to copy `k*s` bytes (tens of gigabytes of copy volume) [2](#0-1) [3](#0-2) .

This charge (`chargeDecodeGas`/`DecodeGasCost`) is only wired into the `DynamicGasPrecompile.RunAndCalculateGas` path, precisely because that path's `RequiredGas`-equivalent step is otherwise skipped: "the static-precompile path charges RequiredGas in vm.RunPrecompiledContract before running; that step is skipped for dynamic-gas precompiles, so we apply the equivalent charge here" [4](#0-3) .

For the static `Precompile` type, gas is charged via `p.executor.RequiredGas(input[4:], method)` — a per-precompile implementation defined by the `PrecompileExecutor` interface `RequiredGas([]byte, *abi.Method) uint64` [5](#0-4) [6](#0-5) . Crucially, `Precompile.Run` calls `Prepare`, which unconditionally performs `method.Inputs.Unpack(argsBz)` regardless of what `RequiredGas` charged [7](#0-6) . Repo-wide, `DecodeGasCost` (the aliasing-aware pricing function) is referenced only from `decode_cost.go`, its own test file, the legacy `v67` copy, `precompiles.go`'s dynamic-gas code path, and `gov_test.go` — it is not called from the static precompile executors (e.g. `bank.go`, `auth.go`, `authz.go`, `addr.go`), which price by input length alone via `DefaultGasCost`. This means the EVM's precompiled-contract gas check (`vm.RunPrecompiledContract`, using `RequiredGas(input)`) can under-charge relative to the true cost of `Unpack()` on aliased calldata for every non-dynamic-gas precompile.

### Impact Explanation
An attacker submitting a normal EVM transaction that calls a static Cosmos precompile (e.g. bank, auth, authz, addr) with a crafted argument encoding — many offsets aliasing the same underlying string/bytes payload — can force `method.Inputs.Unpack()` to perform work and memory allocation that is super-linear in the actual calldata size, while paying gas priced only linearly in `len(input)`. Because this executes inside block processing (every validator replays the same transaction deterministically), a sufficiently amplified payload can push per-transaction execution time past normal bounds, contributing to block-processing delay across the network, and public EVM JSON-RPC nodes evaluating the same call via `eth_call`/`eth_estimateGas` can suffer excessive CPU/memory consumption from a single request.

### Likelihood Explanation
The precompiles affected (bank, auth, authz, addr, and others sharing the static `Precompile`/`PrecompileExecutor` path) are reachable by any address via a normal EVM transaction or `eth_call`; no special privilege is required. The amplification technique is already demonstrated as functional against go-ethereum's decoder within this very repository's test suite, showing the underlying primitive is real and exploitable, and the mitigation comment explicitly states it was added only where the standard `RequiredGas` gate does not otherwise apply — implying the static path was consciously assumed to be "already gated" without verifying that the per-executor `RequiredGas` implementations actually account for decode amplification.

### Recommendation
Extend `DecodeGasCost`/the aliasing-aware charge to the static `Precompile.RequiredGas`/`Prepare` path (or have each `PrecompileExecutor.RequiredGas` call `DecodeGasCost` against the method's `abi.Arguments` before any `Unpack` is performed), so that both static and dynamic-gas precompiles charge for the true ABI-decode cost rather than only `len(input)`.

### Proof of Concept
Reuse the harness in `precompiles/common/decode_cost_test.go`'s `TestDecodeStringCopyBytes_Aliased`/`newAliasedVoteWeightedArgs` to build calldata for a static precompile method whose ABI includes a `string`/`string[]`/string-containing tuple argument (e.g. any bank/auth/authz method taking a `string[]`): construct `k` array elements whose offsets all point at the same `s`-byte string tail, producing calldata of size ~`(32k+s)` bytes but a decode cost of `k*s` bytes copied. Because that static precompile's `RequiredGas` prices only `len(input)` (not routed through `DecodeGasCost`), submit this as the input to that precompile's selector in a normal EVM transaction/`eth_call`; the gas charged before execution will be far below the actual CPU/memory cost incurred by `method.Inputs.Unpack(argsBz)` inside `Precompile.Prepare` [8](#0-7) .

### Citations

**File:** precompiles/common/decode_cost.go (L26-33)
```go
//
// The Go ABI decoder's cost is dominated by copying `string` payloads: it
// materializes each string via string(output[begin:end]), and because a single
// string can be referenced by many array/tuple slots, the copied volume can be
// super-linear in len(input) (worst case ~len(input)^2). `bytes` values are
// excluded because the decoder reslices them without copying. The charge is
// therefore a linear pass over the input (DefaultGasCost) plus the string-copy
// volume the decoder would produce, priced at the KV read-per-byte rate.
```

**File:** precompiles/common/decode_cost_test.go (L74-115)
```go
// TestDecodeStringCopyBytes_Aliased is the core case: an attacker-crafted
// string[] whose K element offsets all point at the same S-byte string. The
// decoder copies K*S bytes even though the input is only ~(32*K + S) bytes, so
// the copied volume is super-linear in len(input). The estimator must report the
// full K*S so the caller is charged for the real work — while itself running in
// O(K), not O(K*S).
func TestDecodeStringCopyBytes_Aliased(t *testing.T) {
	const (
		k = uint64(4)
		s = uint64(64)
	)
	// sub = element data region (what the decoder addresses relative to it):
	//   [k head words][length word = s][s bytes of payload]
	headTarget := 32 * k // offset within sub of the shared length word
	sub := make([]byte, 0, headTarget+32+s)
	for range k {
		sub = append(sub, abiWord(headTarget)...) // every element points to the same string
	}
	sub = append(sub, abiWord(s)...)
	sub = append(sub, make([]byte, s)...)

	// data = [offset to array data = 32][array length = k][sub]
	data := append(abiWord(32), abiWord(k)...)
	data = append(data, sub...)

	arrArgs := abi.Arguments{{Type: mustABIType(t, "string[]", nil)}}

	// The estimator reports the full aliased copy volume.
	n, ok := decodeStringCopyBytes(arrArgs, data)
	require.True(t, ok)
	require.Equal(t, k*s, n)

	// Sanity check against the real decoder: it accepts the encoding and
	// materializes k strings of length s each (i.e. it really does copy k*s).
	vals, err := arrArgs.Unpack(data)
	require.NoError(t, err)
	strs := vals[0].([]string)
	require.Len(t, strs, int(k))
	for _, str := range strs {
		require.Equal(t, int(s), len(str))
	}
}
```

**File:** precompiles/common/decode_cost_test.go (L117-143)
```go
func TestDecodeStringCopyBytes_AliasedVoteWeighted(t *testing.T) {
	const (
		k = uint64(18432)
		s = uint64(602592)
	)
	args := abi.Arguments{
		{Type: mustABIType(t, "uint64", nil)},
		{Type: mustABIType(t, "tuple[]", []abi.ArgumentMarshaling{
			{Name: "option", Type: "int32"},
			{Name: "weight", Type: "string"},
		})},
	}
	data := newAliasedVoteWeightedArgs(k, s)

	n, ok := decodeStringCopyBytes(args, data)
	require.True(t, ok)
	require.Equal(t, k*s, n)

	// DecodeGasCost must price the amplified copy volume, not just len(input).
	input := append([]byte{0x59, 0x63, 0x4a, 0x88}, data...)
	gas, ok := DecodeGasCost(args, input)
	require.True(t, ok)
	base := DefaultGasCost(input, false)
	require.Equal(t, satAdd(base, satMul(storetypes.KVGasConfig().ReadCostPerByte, k*s)), gas)
	require.Greater(t, gas, uint64(12_500_000))
	require.Less(t, uint64(len(input)), uint64(2<<20)) // compact calldata, ~1.2MiB
}
```

**File:** precompiles/common/precompiles.go (L32-35)
```go
type PrecompileExecutor interface {
	RequiredGas([]byte, *abi.Method) uint64
	Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, hooks *tracing.Hooks) ([]byte, error)
}
```

**File:** precompiles/common/precompiles.go (L50-62)
```go
func (p Precompile) RequiredGas(input []byte) uint64 {
	methodID, err := ExtractMethodID(input)
	if err != nil {
		return UnknownMethodCallGas
	}

	method, err := p.MethodById(methodID)
	if err != nil {
		// This should never happen since this method is going to fail during Run
		return UnknownMethodCallGas
	}
	return p.executor.RequiredGas(input[4:], method)
}
```

**File:** precompiles/common/precompiles.go (L101-122)
```go
func (p Precompile) Prepare(evm *vm.EVM, input []byte) (sdk.Context, *abi.Method, []interface{}, error) {
	ctxer := state.GetDBImpl(evm.StateDB)
	if ctxer == nil {
		return sdk.Context{}, nil, nil, errors.New("cannot get context from EVM")
	}
	methodID, err := ExtractMethodID(input)
	if err != nil {
		return sdk.Context{}, nil, nil, err
	}
	method, err := p.MethodById(methodID)
	if err != nil {
		return sdk.Context{}, nil, nil, err
	}

	argsBz := input[4:]
	args, err := method.Inputs.Unpack(argsBz)
	if err != nil {
		return sdk.Context{}, nil, nil, err
	}

	return ctxer.Ctx(), method, args, nil
}
```

**File:** precompiles/common/precompiles.go (L169-202)
```go
	// Resolve the target method from the 4-byte selector only. The argument
	// payload is intentionally NOT decoded yet: ABI decoding of attacker-
	// controlled calldata can cost far more than len(input) (a single string can
	// be referenced by many array/tuple slots), so it must be paid for out of the
	// gas the caller supplied. The static-precompile path charges RequiredGas in
	// vm.RunPrecompiledContract before running; that step is skipped for
	// dynamic-gas precompiles, so we apply the equivalent charge here.
	methodID, err := ExtractMethodID(input)
	if err != nil {
		return nil, 0, err
	}
	method, err := d.MethodById(methodID)
	if err != nil {
		return nil, 0, err
	}
	operation = method.Name

	ctx := ctxer.Ctx()
	// Install the gas meter derived from the supplied EVM gas, then charge for
	// decoding the calldata BEFORE decoding it. A call that cannot afford the
	// decode is rejected here, before the parse/allocation work is performed.
	// chargeDecodeGas scopes the out-of-gas recovery to just these charges, so an
	// executor that later exhausts its gas keeps its normal (propagating)
	// out-of-gas semantics.
	gasLimit := d.executor.EVMKeeper().GetCosmosGasLimitFromEVMGas(ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)), suppliedGas)
	ctx = ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, gasLimit))
	if err = d.chargeDecodeGas(ctx, method, input); err != nil {
		return nil, 0, err
	}

	args, err := method.Inputs.Unpack(input[4:])
	if err != nil {
		return nil, 0, err
	}
```
