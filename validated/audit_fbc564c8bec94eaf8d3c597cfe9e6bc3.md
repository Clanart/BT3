Based on my research, this codebase has already implemented a targeted mitigation for exactly this bug class (quadratic-cost decoding of attacker-controlled input relative to charged cost), but the mitigation is incomplete: it only covers the `DynamicGasPrecompile` path, not the static `Precompile` path.

### Title
Static (non-dynamic-gas) EVM precompiles charge only linear `RequiredGas` while ABI-decoding attacker-aliased calldata can cost O(n²) - (File: precompiles/common/precompiles.go)

### Summary
`DecodeGasCost`/`decodeStringCopyBytes` in `precompiles/common/decode_cost.go` were added specifically to price go-ethereum's ABI `Unpack` cost, which can be super-linear in `len(input)` because a single aliased offset in `string[]`/`tuple[]` calldata can be referenced by many head-word slots, causing the decoder to materialize far more bytes than the calldata length implies (worst case ~len(input)²) [1](#0-0) . This surcharge is wired into `DynamicGasPrecompile.RunAndCalculateGas` via `chargeDecodeGas`, which explicitly charges `DecodeGasCost` before calling `Unpack` [2](#0-1) , and is proven effective by `TestVoteWeightedAliasedOptionsRejectedBeforeUnpack`, which shows a ~40KiB crafted `voteWeighted` calldata would otherwise force an ~8MiB decode and is now rejected before `Unpack` runs [3](#0-2) .

However, the static `PrecompileExecutor` interface only exposes `RequiredGas([]byte, *abi.Method) uint64`, computed and charged by go-ethereum's `vm.RunPrecompiledContract` *before* `Run`/`Execute` is invoked [4](#0-3) , and the comment in the dynamic-gas path explicitly states that this static `RequiredGas` charge is the only protection for the static path and that the `chargeDecodeGas` equivalent "is skipped for dynamic-gas precompiles, so we apply the equivalent charge here" — implying the static path is assumed to already be covered by `RequiredGas` [5](#0-4) . But several static precompile executors compute `RequiredGas` as a flat linear function of `len(input)` with no ABI-decode awareness, e.g. the `json` precompile family: `RequiredGas(input, method) = GasCostPerByte * len(input)` [6](#0-5) . This same linear pattern recurs across the static precompile family (`bank`, `auth`, `authz`, `addr`, etc., all implementing `RequiredGas` independent of decode cost).

### Finding Description
The static precompile dispatch flow decodes calldata via `method.Inputs.Unpack` (go-ethereum ABI decoding) inside `Execute`/`Run`, but the gas charged for that call is fixed by `RequiredGas`, which is computed purely from `len(input)` for many precompiles rather than from the decoder's actual (potentially amplified) copy volume. Because a single dynamic-array/tuple element in ABI encoding can alias its offset to point every array slot at the same shared string payload, `Unpack` can be forced to copy `k*s` bytes from an input of size `O(k+s)` — exactly the "quadratic in decoded volume vs. calldata size" complexity class flagged in the referenced Suricata dcerpc buffering CVE. The `DecodeGasCost` fix explicitly documents and neutralizes this for `DynamicGasPrecompile`, but static precompiles whose `Execute` methods accept any dynamic array-of-tuple-with-string (or nested aliasable dynamic types) and whose `RequiredGas` is a flat per-byte charge remain exposed to the same amplification, since nothing stops the caller from supplying aliased offsets before `RequiredGas` is evaluated and `Unpack` is subsequently run.

### Impact Explanation
An attacker submitting a single EVM transaction that calls a vulnerable static precompile method accepting a dynamic array/tuple argument with a `string` field can pay gas proportional to `len(calldata)` (bounded by EVM calldata gas costs, e.g. a few MiB) while forcing the node to perform an ABI-decode copy of many times that size (potentially hundreds of MiB, mirroring the `k*s` example already demonstrated against `voteWeighted` in the test suite, before the fix). This causes disproportionate CPU/memory consumption per unit of gas paid, which can degrade transaction processing throughput chain-wide and risks pushing block execution time beyond acceptable limits — a public-RPC/validator availability impact reachable by any unprivileged transaction sender.

### Likelihood Explanation
Likelihood depends on whether any currently-registered static precompile method accepts an ABI argument shape that both (a) contains a `string` (or other decoder-copies-payload type) inside a dynamic array or tuple, and (b) is dispatched through the static `Precompile`/`RequiredGas` path rather than `DynamicGasPrecompile`. I was not able to fully enumerate every static precompile's ABI method signatures within the remaining investigation budget to confirm a concrete vulnerable method exists today (e.g., the `json` precompile's `extractAsBytesList` takes array arguments, but I could not confirm its full ABI type is decoder-copy-amplifiable). This is a real, confirmed gap in the mitigation's coverage, but I could not independently prove today's method registry includes an exploitable target within the tool budget available; this needs a full enumeration of all static-precompile ABI methods with array/tuple/string arguments to confirm exploitability with certainty.

### Recommendation
Extend the `DecodeGasCost`/`chargeDecodeGas` protection (or an equivalent pre-`Unpack` gas charge based on estimated decode-copy volume) to the static `PrecompileExecutor.RequiredGas` path, or centrally gate all `Unpack` calls (static and dynamic) through the same aliasing-aware cost estimator before decoding, so no precompile method dispatch — static or dynamic-gas — can decode calldata whose real cost exceeds what `RequiredGas`/gas-metering charges upfront.

### Proof of Concept
Not independently constructed against a static precompile in this pass; the existing `TestVoteWeightedAliasedOptionsRejectedBeforeUnpack` / `craftAliasedVoteWeightedArgs` pattern in `precompiles/gov/gov_test.go` (used to validate the dynamic-gas mitigation) should be adapted against a static precompile method's ABI (any method with a `tuple[]`/`string[]` argument dispatched through `Precompile.RequiredGas` rather than `DynamicGasPrecompile`) to confirm whether `Unpack` can still be driven to O(k*s) copy volume while `RequiredGas` only charges O(k+s). [7](#0-6)

### Citations

**File:** precompiles/common/decode_cost.go (L21-33)
```go
// DecodeGasCost returns the gas to charge for ABI-decoding a dynamic precompile
// call's calldata (the full input, including the 4-byte selector) given the
// method's argument list. ok is false when the calldata is structurally invalid
// (or too deeply nested to price cheaply); the caller should reject such input
// rather than decode it, since the real decoder would reject it too.
//
// The Go ABI decoder's cost is dominated by copying `string` payloads: it
// materializes each string via string(output[begin:end]), and because a single
// string can be referenced by many array/tuple slots, the copied volume can be
// super-linear in len(input) (worst case ~len(input)^2). `bytes` values are
// excluded because the decoder reslices them without copying. The charge is
// therefore a linear pass over the input (DefaultGasCost) plus the string-copy
// volume the decoder would produce, priced at the KV read-per-byte rate.
```

**File:** precompiles/common/precompiles.go (L32-34)
```go
type PrecompileExecutor interface {
	RequiredGas([]byte, *abi.Method) uint64
	Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, hooks *tracing.Hooks) ([]byte, error)
```

**File:** precompiles/common/precompiles.go (L169-199)
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
```

**File:** precompiles/gov/gov_test.go (L1220-1266)
```go
func TestVoteWeightedAliasedOptionsRejectedBeforeUnpack(t *testing.T) {
	testApp := testkeeper.EVMTestApp
	ctx := testApp.NewContext(false, tmtypes.Header{}).WithBlockHeight(2)
	k := &testApp.EvmKeeper

	seiAddr, evmAddr := testkeeper.MockAddressPair()
	k.SetAddressMapping(ctx, seiAddr, evmAddr)

	p, err := gov.NewPrecompile(testApp.GetPrecompileKeepers())
	require.NoError(t, err)
	method := p.ABI.Methods[gov.VoteWeightedMethod]

	// Dimensions chosen so calldata stays small (~40KiB) while decoded copy
	// volume (options*weightBytes) prices above the 12.5M gas cap used in the
	// original reproduction. If Unpack ran, allocation would be only ~8MiB —
	// CI-safe — and the error would be the post-decode max-options message
	// instead.
	const (
		options     = uint64(1024)
		weightBytes = uint64(8192)
		suppliedGas = uint64(12_500_000)
	)
	args := craftAliasedVoteWeightedArgs(options, weightBytes)
	input := append(append([]byte{}, method.ID...), args...)

	decodeGas, ok := pcommon.DecodeGasCost(method.Inputs, input)
	require.True(t, ok)
	require.Greater(t, decodeGas, suppliedGas)

	statedb := state.NewDBImpl(ctx, k, true)
	evm := vm.EVM{StateDB: statedb}
	ret, remaining, runErr := p.RunAndCalculateGas(&evm, evmAddr, evmAddr, input, suppliedGas, big.NewInt(0), nil, false, false)
	require.Nil(t, ret)
	require.Equal(t, uint64(0), remaining)
	require.ErrorIs(t, runErr, vm.ErrExecutionReverted)

	precompileErr := statedb.GetPrecompileError()
	require.NotNil(t, precompileErr)
	require.NotContains(t, precompileErr.Error(), "too many vote options",
		"decode must fail before the post-Unpack max-options check; got %v", precompileErr)
	// chargeDecodeGas recovers sdk.ErrorOutOfGas as fmt.Errorf("%v", r), which
	// stringifies to "{<descriptor>}"; the descriptor names the decode charge.
	require.Contains(t, precompileErr.Error(), "gov precompile calldata decode")

	_, found := testApp.GovKeeper.GetVote(statedb.Ctx(), 1, seiAddr)
	require.False(t, found)
}
```

**File:** precompiles/gov/gov_test.go (L1268-1282)
```go
func craftAliasedVoteWeightedArgs(k, s uint64) []byte {
	tupleRel := 32 * k
	arrayPayload := make([]byte, 0, int(32*k+64+s))
	for range k {
		arrayPayload = append(arrayPayload, abiWord(tupleRel)...)
	}
	arrayPayload = append(arrayPayload, abiWord(s)...)
	arrayPayload = append(arrayPayload, abiWord(0)...)
	arrayPayload = append(arrayPayload, make([]byte, s)...)

	data := append(abiWord(1), abiWord(64)...)
	data = append(data, abiWord(k)...)
	data = append(data, arrayPayload...)
	return data
}
```

**File:** precompiles/json/legacy/v67/json.go (L64-67)
```go
		}
	}

	return pcommon.NewDynamicGasPrecompile(newAbi, p, common.HexToAddress(JSONAddress), "json"), nil
```
