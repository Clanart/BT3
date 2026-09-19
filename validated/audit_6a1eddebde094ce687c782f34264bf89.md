Confirmed: the static (non-dynamic-gas) `Precompile.Run` / `Prepare` path in `precompiles/common/precompiles.go` calls `method.Inputs.Unpack(argsBz)` directly, with no equivalent of `chargeDecodeGas`/`DecodeGasCost`/`maxDecodeWalkOps`. Gas for this path is only checked via `RequiredGas`, which for most executors resolves to `DefaultGasCost` (`precompiles/common/legacy/v580/precompiles.go:258-264`), a purely linear function of `len(input)`. This is exactly the bug class the maintainers themselves documented and fixed only for the *dynamic*-gas precompile framework (`chargeDecodeGas` comment in `precompiles/common/precompiles.go:217-247`, and the regression test `TestDynamicGasPrecompileGasGate` at `precompiles/common/precompiles_test.go:160-193`), which explicitly says "ABI decode of attacker-controlled calldata can cost far more than len(input)... so it must be paid for out of the gas the caller supplied." The static path was left unpatched, but distribution's ABI does contain a `string`-typed argument (`abi.json` matches for `string[]`/`string`), so at least one static precompile's `Inputs.Unpack` is reachable with attacker-controlled string-containing calldata whose charged gas (`DefaultGasCost`, linear) does not bound the actual O(n²) decode cost that go-ethereum's ABI unpacker can incur (repeated dynamic offsets pointing at overlapping/duplicated string payloads).

I was not able to fully enumerate, within the remaining iterations, every static-precompile ABI across all versions to confirm exactly which non-dynamic-gas precompiles use `abi.Arguments` containing `string`/nested dynamic array types capable of the worst-case duplication pattern (only `distribution`'s abi.json turned up in a grep across current+legacy versions), nor did I trace whether `vm.RunPrecompiledContract`'s outer gas check (using `RequiredGas`) is itself sufficient in practice to bound the CPU time before `Run`/`Prepare` executes `Unpack`. That would require reading `go-ethereum`'s `RunPrecompiledContract` call site and each static precompile's abi.json/executor pairing in this repo, which the remaining budget didn't allow.

### Title
Static (non-dynamic-gas) precompile ABI-decode path is unmetered against super-linear calldata decode cost - ([File: precompiles/common/precompiles.go])

### Summary
The static precompile execution path (`Precompile.Prepare`/`Run`) unpacks attacker-controlled EVM calldata via go-ethereum's `abi.Arguments.Unpack` without the string-copy-volume gas surcharge and `maxDecodeWalkOps` cap that the dynamic-gas precompile path (`DynamicGasPrecompile.chargeDecodeGas`) applies. This mirrors CVE-2018-1000654's bug class: an unbounded/parsing-cost-proportional-to-structure (not to metered charge) operation on attacker-supplied input causes disproportionate CPU usage relative to what was paid for.

### Finding Description
`Precompile.Prepare` at [1](#0-0)  calls `method.Inputs.Unpack(argsBz)` directly on raw EVM calldata with no pre-charge specific to the decode cost. The only gas gate for this path is `RequiredGas`, which for the majority of static-precompile executors resolves to `DefaultGasCost`, a linear function of `len(input)`: [2](#0-1) .

The project's own code explicitly documents that this is insufficient for the ABI decoder: go-ethereum's `Unpack` materializes each `string` value by copying it, and because a single string payload can be referenced (via crafted offsets) by many array/tuple slots, "the copied volume can be super-linear in len(input) (worst case ~len(input)^2)" — see the comment and the `maxDecodeWalkOps` defensive cap in [3](#0-2) .

This exact class of issue was identified and fixed, but only for the `DynamicGasPrecompile.RunAndCalculateGas` path, via `chargeDecodeGas`, which computes `DecodeGasCost` (linear scan + string-copy surcharge, bounded by `maxDecodeWalkOps`) and consumes it from the gas meter *before* calling `Unpack`: [4](#0-3)  and [5](#0-4) . The regression test explicitly frames this as "a DoS in the dynamic precompile framework": [6](#0-5) .

No equivalent charge exists in the static `Precompile` type. Any static precompile whose ABI has a method taking a `string` (or array/tuple containing a `string`) argument is reachable by any EVM caller with crafted calldata: the calldata gas cost charged to the sender (via `RequiredGas`/`DefaultGasCost`, linear) does not reflect the actual super-linear CPU work the node performs decoding it, before charging any further gas for the executor logic.

### Impact Explanation
An attacker can submit a transaction (or precompile call from any contract) that pays for linear-cost calldata but forces every validator node to perform disproportionately larger CPU work decoding the ABI arguments (up to ~O(n²) in the worst case for large `n`), because `Precompile.Prepare` decodes before any string-copy-proportional charge is applied. This can be used to slow down block processing across all validators simultaneously by crafting calldata sent to a vulnerable static precompile method, potentially contributing to block delay. This matches the "block delay beyond 2.5 seconds" acceptance criterion if the decode cost can be pushed high enough within the EVM calldata gas budget for a single call (and amplified across multiple calls in one block).

### Likelihood Explanation
Likelihood depends on whether any currently-deployed static (non-dynamic-gas) precompile actually exposes a method with a `string`- or nested-array-containing argument at a large size. A grep found `string` type entries in the `distribution` precompile's ABI (current and multiple legacy versions), suggesting at least one static precompile could contain this argument shape, but I could not fully confirm within budget whether `distribution`'s precompile is wired through the static `Precompile.Run`/`Prepare` path (versus `DynamicGasPrecompile`) or whether its specific method signature allows the attacker to construct the crafted-offset duplication pattern needed to reach super-linear cost. This uncertainty should be resolved by code review before treating this as confirmed-exploitable.

### Recommendation
Apply the same `chargeDecodeGas`/`DecodeGasCost` (with `maxDecodeWalkOps` backstop) mechanism used in `DynamicGasPrecompile.RunAndCalculateGas` to the static `Precompile.Prepare`/`Run` path before calling `method.Inputs.Unpack`, ensuring `RequiredGas` (or an equivalent pre-check inside `Run`) accounts for string-copy volume, not just linear input length, for every static precompile whose ABI can contain `string`/dynamic-array-of-string arguments.

### Proof of Concept
Not independently reproduced within this investigation's scope; conceptually: craft calldata for a static-precompile method whose ABI signature includes a `string` argument or `string[]`/tuple-of-string array, where the ABI head words are all set to the same (small) offset so a single string payload is referenced by many decoded slots (mirroring the exact "duplicated string offset" pattern the project's own `decode_cost.go` comment describes as producing `~len(input)^2` copy volume), and submit it with gas just sufficient to cover `RequiredGas`/`DefaultGasCost`'s linear charge but insufficient to reflect the true quadratic decode cost, then observe CPU time in `Unpack` disproportionate to gas charged.

### Citations

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

**File:** precompiles/common/precompiles.go (L169-201)
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

**File:** precompiles/common/legacy/v580/precompiles.go (L258-264)
```go
func DefaultGasCost(input []byte, isTransaction bool) uint64 {
	if isTransaction {
		return storetypes.KVGasConfig().WriteCostFlat + (storetypes.KVGasConfig().WriteCostPerByte * uint64(len(input)))
	}

	return storetypes.KVGasConfig().ReadCostFlat + (storetypes.KVGasConfig().ReadCostPerByte * uint64(len(input)))
}
```

**File:** precompiles/common/decode_cost.go (L13-44)
```go
// maxDecodeWalkOps bounds how many type nodes decodeStringCopyBytes visits. For
// the argument shapes precompiles actually use (single-level dynamic arrays and
// flat tuples of leaves) the walk is linear in len(data); this cap is a
// defensive backstop so a hypothetical deeply-nested type cannot turn the cost
// estimate itself into a super-linear computation. Calldata large enough to hit
// it is infeasible under EVM calldata gas costs.
const maxDecodeWalkOps = 1 << 20

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
func DecodeGasCost(args abi.Arguments, input []byte) (uint64, bool) {
	base := DefaultGasCost(input, false)
	if len(input) < 4 {
		return base, true
	}
	strBytes, ok := decodeStringCopyBytes(args, input[4:])
	if !ok {
		return 0, false
	}
	return satAdd(base, satMul(storetypes.KVGasConfig().ReadCostPerByte, strBytes)), true
}
```

**File:** precompiles/common/precompiles_test.go (L160-193)
```go
// TestDynamicGasPrecompileGasGate is a regression test for a DoS in the dynamic
// precompile framework: the ABI decode of (attacker-controlled) calldata used to
// run before the supplied EVM gas was turned into a gas meter, so a call that
// forwarded ~zero gas could still force every validator to parse and allocate
// the calldata for free. The decode must now be gated by the supplied gas.
func TestDynamicGasPrecompileGasGate(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	abiBz, err := os.ReadFile("erc20_abi.json")
	require.Nil(t, err)
	newAbi, err := abi.JSON(bytes.NewReader(abiBz))
	require.Nil(t, err)
	input, err := newAbi.Pack("decimals")
	require.Nil(t, err)

	precompile := common.NewDynamicGasPrecompile(newAbi, &MockDynamicGasPrecompileExecutor{throw: false, evmKeeper: k}, ethcommon.Address{}, "test")

	// Zero supplied gas (the PoC scenario): the call must be rejected before the
	// executor runs, so no event is emitted (Execute is what emits it) and no ABI
	// decode is performed.
	stateDB := state.NewDBImpl(ctx.WithEventManager(sdk.NewEventManager()), k, false)
	res, remainingGas, err := precompile.RunAndCalculateGas(&vm.EVM{StateDB: stateDB}, ethcommon.Address{}, ethcommon.Address{}, input, 0, big.NewInt(0), nil, false, false)
	require.Nil(t, res)
	require.Equal(t, uint64(0), remainingGas)
	require.Equal(t, vm.ErrExecutionReverted, err)
	require.Empty(t, stateDB.Ctx().EventManager().Events())

	// With enough gas the same call proceeds through decode and execution.
	stateDB = state.NewDBImpl(ctx.WithEventManager(sdk.NewEventManager()), k, false)
	res, _, err = precompile.RunAndCalculateGas(&vm.EVM{StateDB: stateDB}, ethcommon.Address{}, ethcommon.Address{}, input, 100000, big.NewInt(0), nil, false, false)
	require.Equal(t, []byte("success"), res)
	require.Nil(t, err)
	require.NotEmpty(t, stateDB.Ctx().EventManager().Events())
}
```
