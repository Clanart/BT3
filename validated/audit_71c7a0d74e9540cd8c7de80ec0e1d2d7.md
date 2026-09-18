### Title
Unbounded ABI-decode memory amplification in static (non-dynamic-gas) precompiles bypasses gas metering - (File: precompiles/common/precompiles.go)

### Summary
The libtiff CVE is a classic "small crafted input triggers disproportionately large allocation" out-of-memory bug. Sei-chain has an internal analog in its EVM precompile calldata-decoding path. For `DynamicGasPrecompile` executors (e.g. `gov`), the codebase explicitly recognizes and fixes this exact bug class: go-ethereum's ABI `Unpack` can copy `string` payload bytes a number of times proportional to how many array/tuple slots reference the same offset, making decode cost up to quadratic in `len(input)` rather than linear. This is mitigated there via `chargeDecodeGas`/`DecodeGasCost`, which is charged *before* `Unpack` is called [1](#0-0) . However, the plain `Precompile.Prepare` used by static (non-dynamic-gas) precompiles — e.g. `bank`, `auth`, `authz`, `addr` — calls `method.Inputs.Unpack(argsBz)` directly with no equivalent decode-cost charge [2](#0-1) .

### Finding Description
Static precompiles are gas-metered only through `RequiredGas`, which is charged by go-ethereum's `vm.RunPrecompiledContract` before `Run`/`Prepare` executes [3](#0-2) . The legacy implementations of `RequiredGas` (kept as the template for the pattern) charge a flat cost plus a cost linear in `len(argsBz)`, e.g. `WriteCostFlat + WriteCostPerByte*len(argsBz)` [4](#0-3) .

The codebase's own documentation of the bug class explains why a linear/length-based charge is insufficient: `decodeStringCopyBytes` in `precompiles/common/decode_cost.go` exists specifically because "a single string can be referenced by many array/tuple slots, [so] the copied volume can be super-linear in len(input) (worst case ~len(input)^2)" [5](#0-4) . This is precisely why `DynamicGasPrecompile.RunAndCalculateGas` inserts `chargeDecodeGas` before calling `Unpack` [1](#0-0) , and a dedicated regression test (`TestVoteWeightedAliasedOptionsRejectedBeforeUnpack`) proves that without this charge, aliased ABI offsets let a ~40KiB calldata payload cause many megabytes of decoded string-copy volume for only ~12.5M gas [6](#0-5) .

The static `Precompile.Prepare` path never received this fix: it unconditionally calls `method.Inputs.Unpack(argsBz)` with only the flat/per-byte `RequiredGas` already paid [2](#0-1) . Any static precompile method that takes a `string` parameter (e.g. `denom` arguments common in `bank`/`tokenfactory`-adjacent precompiles, or memo/description fields in `auth`/`authz`) can be called with calldata whose offsets alias the same string payload from many array/tuple slots, causing the same quadratic-in-input-size decode-copy blowup identified and fixed for `gov`, but here uncharged.

### Impact Explanation
An attacker submitting a normal EVM transaction that calls a static (non-dynamic-gas) precompile with crafted, offset-aliased ABI calldata containing a `string` argument can force the node executing/validating the transaction to allocate memory far beyond what the linear `RequiredGas` charge accounts for. Depending on the achievable amplification factor for the calldata sizes affordable under the EVM's per-transaction gas/calldata limits, this can consume excessive heap memory during transaction execution on every validator (and any RPC/full node re-executing or simulating the tx), which can slow block processing beyond acceptable limits or, in the worst case, exhaust memory and crash/OOM-kill the node process — a validator-halt / DoS condition reachable from a single unprivileged transaction.

### Likelihood Explanation
Moderate. This requires: (a) a static precompile exposing a method with a `string` (or `string[]`/tuple-of-strings) parameter, and (b) crafting ABI-encoded calldata where the offset table points multiple array/tuple slots at the same string payload so `Unpack` copies it repeatedly. The technique itself is already demonstrated and proven exploitable in this exact codebase against `gov`'s `voteWeighted` method before the fix was added (`TestVoteWeightedAliasedOptionsRejectedBeforeUnpack`), so the amplification mechanics are confirmed feasible under real EVM gas/calldata constraints. Applying the same crafted-calldata technique to any static precompile method taking string arguments is a straightforward transaction anyone can submit; no special privilege is required.

### Recommendation
Apply the same `chargeDecodeGas`/`DecodeGasCost` protection used in `DynamicGasPrecompile.RunAndCalculateGas` to the static `Precompile.Prepare`/`Run` path — charge the string-copy decode cost against the gas already reserved by `RequiredGas` (or fold it into `RequiredGas` itself) before calling `method.Inputs.Unpack` in `precompiles/common/precompiles.go`. Audit every static precompile (`bank`, `auth`, `authz`, `addr`, and any others still on `common.NewPrecompile`) for methods with `string`/dynamic-array-of-string parameters and confirm none can be driven into a materially super-linear decode without matching gas cost.

### Proof of Concept
Conceptually mirror `craftAliasedVoteWeightedArgs` in `precompiles/gov/gov_test.go` (lines 1268-1282) against a static precompile method that has a `string` (or array-of-strings) parameter: build the ABI offset table so many outer slots reference the same underlying dynamic string payload, keeping total calldata small enough to stay within normal EVM calldata/gas limits, then invoke the precompile via an EVM transaction. Since `RequiredGas` for the static path only charges `flat + perByte*len(argsBz)` while `Prepare` calls `method.Inputs.Unpack` unprotected, the decode step performs many times more byte-copy work/memory than the length-based charge implies — reproducing the same disproportionate-allocation-from-small-input pattern as CVE-2023-6277, but on Sei's EVM precompile execution path.

### Citations

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

**File:** precompiles/common/precompiles.go (L101-121)
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

**File:** precompiles/common/legacy/v555/precompiles.go (L27-35)
```go
func (p Precompile) RequiredGas(input []byte, isTransaction bool) uint64 {
	argsBz := input[4:] // first four bytes are method ID

	if isTransaction {
		return storetypes.KVGasConfig().WriteCostFlat + (storetypes.KVGasConfig().WriteCostPerByte * uint64(len(argsBz)))
	}

	return storetypes.KVGasConfig().ReadCostFlat + (storetypes.KVGasConfig().ReadCostPerByte * uint64(len(argsBz)))
}
```

**File:** precompiles/common/decode_cost.go (L27-34)
```go
// The Go ABI decoder's cost is dominated by copying `string` payloads: it
// materializes each string via string(output[begin:end]), and because a single
// string can be referenced by many array/tuple slots, the copied volume can be
// super-linear in len(input) (worst case ~len(input)^2). `bytes` values are
// excluded because the decoder reslices them without copying. The charge is
// therefore a linear pass over the input (DefaultGasCost) plus the string-copy
// volume the decoder would produce, priced at the KV read-per-byte rate.
func DecodeGasCost(args abi.Arguments, input []byte) (uint64, bool) {
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
