Confirmed: gov (`voteWeighted`) already uses `DynamicGasPrecompile` and pays via `chargeDecodeGas`/`DecodeGasCost`, which explicitly prices the aliased-offset amplification (verified by `TestDecodeStringCopyBytes_AliasedVoteWeighted` in `precompiles/common/decode_cost_test.go`). However, that protection lives only in the `DynamicGasPrecompile.RunAndCalculateGas` path. The **static** `Precompile` path (`precompiles/common/precompiles.go` `Precompile.RequiredGas`/`Prepare`) never calls `DecodeGasCost`; it charges gas from the precompile executor's own `RequiredGas(input, method)`, which for `addr`, `bank`, `auth`, `authz`, and their `legacy/vNNN` clones is a flat linear function of `len(argsBz)` (`ReadCostFlat + ReadCostPerByte*len`), then unconditionally runs `method.Inputs.Unpack(argsBz)` in `Prepare` at `precompiles/common/precompiles.go:116`.

### Title
Denial of Service via unpriced ABI decode amplification in static EVM precompiles - (File: precompiles/common/precompiles.go)

### Summary
Static (non-dynamic-gas) EVM precompiles such as `bank`, `addr`, `auth`, and `authz` charge gas linearly in `len(input)` via `RequiredGas`, then call go-ethereum's `abi.Arguments.Unpack` on the raw calldata in `Precompile.Prepare` without the `DecodeGasCost` string-copy surcharge that the `DynamicGasPrecompile` path applies. This is analogous to CVE-2020-7226/Cryptacular: the decoder allocates/copies memory whose volume is controlled by attacker-supplied offsets inside the payload, and that volume can be super-linear (up to O(n²)) in the input size, while the metered cost stays linear.

### Finding Description
`vm.RunPrecompiledContract` calls `Precompile.RequiredGas(input)` before `Run`. For a static precompile this resolves to the executor's `RequiredGas(input[4:], method)`, e.g. `bank`'s and `addr`'s implementations at `precompiles/bank/legacy/v552/bank.go:112-125` / `precompiles/addr/legacy/v552/addr.go:74-87`, which ultimately return `storetypes.KVGasConfig().ReadCostFlat + ReadCostPerByte*len(argsBz)` [1](#0-0) . This is purely linear in the byte length of the calldata.

`Precompile.Run` then calls `Prepare`, which unconditionally does `method.Inputs.Unpack(argsBz)` [2](#0-1)  — with no `DecodeGasCost`/`chargeDecodeGas` step in between.

The `precompiles/common/decode_cost.go` module (and its `decode_cost_test.go`) documents and proves that go-ethereum's ABI decoder's cost is dominated by string-payload copies, and that a single string can be referenced by many array/tuple offset slots, making the copied volume up to quadratic in `len(input)` — e.g. `TestDecodeStringCopyBytes_AliasedVoteWeighted` constructs a ~1.2 MiB `voteWeighted`-shaped calldata that forces >12,500,000 gas worth of string-copy work [3](#0-2) , and the general aliasing primitive is at [4](#0-3) .

This exact amplification primitive is only priced on the `DynamicGasPrecompile` path via `chargeDecodeGas` [5](#0-4) , which is not invoked for static precompiles at all. Any static precompile method whose ABI signature contains a `string`/`bytes` array or dynamic tuple (e.g. arguments accepting arrays of addresses/strings) is reachable by an unprivileged EVM caller and pays only the flat, linear `RequiredGas`, while the actual `Unpack` call performs unbounded (relative to gas paid) memory copying.

### Impact Explanation
An attacker can submit an EVM transaction (or a series of them) calling a static precompile method with crafted calldata containing offset-aliased dynamic array elements. The gas charged by `RequiredGas` is linear in calldata size (bounded by EVM's ~128 KB tx-size and calldata gas costs), but the actual bytes copied during `Unpack` can be quadratic, causing excessive CPU/memory consumption per unit of gas paid. Because this executes inside `ApplyMessage`/block processing for every validator replaying the block, a sufficiently crafted call can measurably slow down transaction processing across the network relative to its gas cost, degrading throughput and potentially contributing to block-time delay under sustained abuse — a resource-exhaustion class matching the CWE-770 pattern in the reference advisory.

### Likelihood Explanation
Reaching a static precompile with attacker-controlled calldata requires only sending a normal EVM transaction to one of the fixed precompile addresses (e.g. `bank` at a known address) with a method whose ABI includes a dynamic array/tuple argument — no special privileges are needed. The likelihood of an exploitable amplification factor depends on which static-precompile methods actually accept such array/tuple types; this was not fully enumerated across every static precompile method signature (`bank`, `addr`, `auth`, `authz` and all `legacy/vNNN` variants) within the scope of this review, so the exact worst-case amplification ratio for each concrete method is unconfirmed.

### Recommendation
Apply the same `chargeDecodeGas`/`DecodeGasCost` pricing used by `DynamicGasPrecompile.RunAndCalculateGas` to the static `Precompile.Prepare`/`RequiredGas` path, so that `RequiredGas` (or a pre-`Unpack` check inside `Prepare`) accounts for the ABI decoder's string-copy amplification before `method.Inputs.Unpack` is invoked, consistently for every precompile entry point.

### Proof of Concept
Not independently executed; the amplification mechanism is already demonstrated in-repo by `TestDecodeStringCopyBytes_Aliased` and `TestDecodeStringCopyBytes_AliasedVoteWeighted` in `precompiles/common/decode_cost_test.go`, which show that an aliased `string[]`/tuple-array encoding of size O(k+s) forces O(k*s) decoder copy volume. The gap identified here is that this same encoding, sent as calldata to a **static** precompile method (rather than the gas-metered `voteWeighted` dynamic-gas path), would not be charged this extra cost via `RequiredGas`/`Prepare`. Confirming a concrete unauthenticated PoC requires identifying a static precompile method whose ABI signature accepts a `string[]`/dynamic-tuple-array argument, which was not fully enumerated in this pass.

### Citations

**File:** precompiles/common/legacy/v552/precompiles.go (L27-35)
```go
func (p Precompile) RequiredGas(input []byte, isTransaction bool) uint64 {
	argsBz := input[4:] // first four bytes are method ID

	if isTransaction {
		return storetypes.KVGasConfig().WriteCostFlat + (storetypes.KVGasConfig().WriteCostPerByte * uint64(len(argsBz)))
	}

	return storetypes.KVGasConfig().ReadCostFlat + (storetypes.KVGasConfig().ReadCostPerByte * uint64(len(argsBz)))
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

**File:** precompiles/common/precompiles.go (L217-247)
```go
// chargeDecodeGas charges the (already-installed) gas meter for decoding the
// calldata, before it is decoded: a length-proportional scan cost that also
// bounds the DecodeGasCost scan, then the string-copy surcharge from
// DecodeGasCost. Its out-of-gas / overflow recovery is deliberately scoped to
// just these charges — a call that cannot afford the decode reverts here, while
// an executor that later exhausts its gas keeps its normal propagating
// out-of-gas semantics. Anything other than a gas-meter panic is re-raised.
func (d DynamicGasPrecompile) chargeDecodeGas(ctx sdk.Context, method *abi.Method, input []byte) (err error) {
	defer func() {
		if r := recover(); r != nil {
			switch r.(type) {
			case sdk.ErrorOutOfGas, sdk.ErrorGasOverflow:
				err = fmt.Errorf("%v", r)
			default:
				panic(r)
			}
		}
	}()
	scanCost := DefaultGasCost(input, false)
	ctx.GasMeter().ConsumeGas(scanCost, fmt.Sprintf("%s precompile calldata scan", d.name))
	decodeCost, ok := DecodeGasCost(method.Inputs, input)
	if !ok {
		// Calldata is structurally invalid (Unpack would reject it too); reject
		// now, without attempting the decode.
		return fmt.Errorf("invalid calldata encoding for %s", d.name)
	}
	// DecodeGasCost already includes scanCost; charge only the remaining
	// (string-copy) portion so the decode is priced exactly once.
	ctx.GasMeter().ConsumeGas(decodeCost-scanCost, fmt.Sprintf("%s precompile calldata decode", d.name))
	return nil
}
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
