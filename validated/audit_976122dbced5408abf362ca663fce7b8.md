### Title
Unbounded ABI-decode cost for static (non-dynamic-gas) precompiles enables CPU-quadratic DoS via crafted calldata - ([File: precompiles/common/precompiles.go])

### Summary
`Precompile.Prepare` (used by every precompile registered with `NewPrecompile`, as opposed to `NewDynamicGasPrecompile`) decodes attacker-controlled calldata with `method.Inputs.Unpack(argsBz)` before any gas is charged for that decode's real cost. The engineering team already identified and fixed this exact class of bug for `DynamicGasPrecompile` (via `chargeDecodeGas`/`DecodeGasCost`), but the static-gas `Precompile` path was left unprotected.

### Finding Description
`DecodeGasCost` and its supporting comments in `precompiles/common/decode_cost.go` explicitly document that go-ethereum's ABI unpacker materializes every `string` payload via `string(output[begin:end])`, and because a single string offset can be referenced by many array/tuple slots in the encoding, "the copied volume can be super-linear in len(input) (worst case ~len(input)^2)". [1](#0-0) 

For `DynamicGasPrecompile.RunAndCalculateGas`, this was mitigated: the method is resolved from the 4-byte selector only, and `chargeDecodeGas` is invoked to charge for the quadratic string-copy cost *before* `method.Inputs.Unpack` is called, explicitly to prevent an attacker from making decode cost exceed the calldata-proportional gas already paid. [2](#0-1) [3](#0-2) 

However, the plain `Precompile.Prepare` function — used by the static-gas precompile path (`bank`, `auth`, `authz`, `staking`, `distribution`, `gov`, `oracle`, `pointer`, `pointerview`, `wasmd`, `json`, `p256`, `addr`, `evidence`, `slashing`, `upgrade`, `solo`, all constructed with `NewPrecompile`, not `NewDynamicGasPrecompile`) — calls `method.Inputs.Unpack(argsBz)` directly with no equivalent decode-cost charge: [4](#0-3) 

The gas actually charged for these calls comes from `Precompile.RequiredGas`, which simply delegates to `p.executor.RequiredGas(input[4:], method)` — a per-method value that (unlike `DecodeGasCost`) is not derived from the string-copy/decode-walk cost model in `decode_cost.go`. [5](#0-4) 

Because EVM precompile gas accounting charges `RequiredGas` up front (in `vm.RunPrecompiledContract`) and that charge is independent of the quadratic decode cost, an attacker can submit calldata for any static-gas precompile method whose ABI signature contains a dynamic array/tuple type that (transitively) contains a `string`, with the offset table crafted so a single string payload is referenced by a very large number of array/tuple slots. `Unpack` will re-materialize the string once per reference, giving O(N²) CPU work for O(N) calldata bytes and O(N) calldata gas paid — the same complexity class as the mistune `~~x~~` repeated-marker bug (unbounded per-token retry/copy cost not memoised or capped in the parser).

### Impact Explanation
Any transaction sender or public JSON-RPC `eth_call`/`eth_sendRawTransaction` client can trigger this by calling a static-gas precompile method with crafted calldata, at a cost proportional only to calldata gas (16 gas/nonzero byte), while the decoding cost run identically by every validator during block execution is quadratic. A single such transaction embedded in a block forces all validators executing that block to pay the same disproportionate CPU cost deterministically, which can push block execution time past the 2.5s target and stall the network, or be used repeatedly to degrade throughput on public RPC nodes performing `eth_call` simulation of the same payload.

### Likelihood Explanation
High reachability: this requires only crafting calldata for common precompile methods (e.g., ones taking `string[]`/tuple arrays such as bank, staking, gov, wasmd) and submitting a normal transaction or RPC call — no special privileges, no consensus assumptions, no reliance on other validators or nodes misbehaving. The bug class is already proven exploitable and quantified in the reference report (~4s CPU at 40KB, quadratic scaling), and the codebase's own comments confirm the identical cost model applies to Go's ABI decoder used here.

### Recommendation
Apply the same `chargeDecodeGas`/`DecodeGasCost` gas-metering already implemented for `DynamicGasPrecompile` to the static `Precompile.Prepare` path before calling `method.Inputs.Unpack`, or alternatively fold the string-copy cost model into `RequiredGas` for all `PrecompileExecutor` implementations so that the pre-charged gas actually reflects the decode's worst-case cost. Add a regression test asserting that decode time/gas for adversarially-repeated string offsets in any static precompile method scales linearly, not quadratically, with input size.

### Proof of Concept
1. Pick any precompile registered via `NewPrecompile` whose ABI has a method taking a dynamic array of structs/strings (e.g., a `string[]` or `Tuple[]` argument).
2. Craft calldata where the array's offset table repeats the same string-payload offset across as many slots as the calldata size budget allows (mirroring the `decodeStringCopyBytes`/`walkElems` traversal each duplicate reference re-walks and go-ethereum's `Unpack` re-copies).
3. Submit as a transaction calling that precompile, or as an `eth_call` to a public RPC node.
4. `RequiredGas` charges only the fixed/method-level gas from `p.executor.RequiredGas`, unrelated to decode cost; `Prepare` then calls `Unpack`, which performs O(N²) string copies for O(N) calldata bytes, consuming disproportionate validator/RPC-node CPU per byte of gas paid.

### Citations

**File:** precompiles/common/decode_cost.go (L27-33)
```go
// The Go ABI decoder's cost is dominated by copying `string` payloads: it
// materializes each string via string(output[begin:end]), and because a single
// string can be referenced by many array/tuple slots, the copied volume can be
// super-linear in len(input) (worst case ~len(input)^2). `bytes` values are
// excluded because the decoder reslices them without copying. The charge is
// therefore a linear pass over the input (DefaultGasCost) plus the string-copy
// volume the decoder would produce, priced at the KV read-per-byte rate.
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
