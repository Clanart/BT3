## Title
Unchecked integer overflow in `multiplierGasMeter.adjustGas` gas-scaling multiplication - (File: sei-cosmos/store/types/gas.go)

### Summary
`multiplierGasMeter.adjustGas` (used by `NewMultiplierGasMeter`, which is wired into every non-simulated CheckTx/DeliverTx via `GetGasMeterSetter` in `app/antedecorators/gas.go` and `app/ante/cosmos_checktx.go`) computes `original * multiplierNumerator / multiplierDenominator` on plain `uint64` values with no overflow guard, unlike every sibling gas-arithmetic path in the codebase.

### Finding Description
`adjustGas` is defined as: [1](#0-0) 

It is called from both `ConsumeGas` and `RefundGas` of `multiplierGasMeter`: [2](#0-1) 

This is the direct analog of the JBCrypt bug class: an unsigned multiplication (`original * multiplierNumerator`) that is not checked for wraparound before being consumed/relied upon, in contrast to the rest of the gas subsystem, which is careful about this exact class of bug:
- `basicGasMeter.ConsumeGas` explicitly uses `addUint64Overflow` before applying gas: [3](#0-2) 
- `infiniteGasMeter.ConsumeGas` does the same: [4](#0-3) 
- The wasm gas register's analogous multiply-by-multiplier function checks for wraparound explicitly: [5](#0-4) 

`NewMultiplierGasMeter` is installed as the transaction-level gas meter for every CheckTx/DeliverTx (non-simulate, non-genesis) via: [6](#0-5) 

If `original * multiplierNumerator` overflows `uint64` before the division by `multiplierDenominator` is applied, the truncated (wrapped) product is divided down, producing an adjusted gas amount that is far smaller than intended for a given input `amount`/`amount` gas usage. Since `ConsumeGas` then compares this wrapped-down `consumed` value against `Limit()`, a large-enough operation gas cost could wrap to a small number and never trip the out-of-gas check, letting the transaction consume unbounded real work while reporting negligible gas consumption.

### Impact Explanation
If the multiplier/gas values used in a given execution allow `original` to approach `math.MaxUint64 / multiplierNumerator`, the resulting overflow silently deflates metered gas, defeating the resource-metering invariant the gas meter exists to enforce. This can be leveraged to:
- Perform gas-limit bypass, letting a transaction execute more computation/storage work than it paid for (resource exhaustion / node-level DoS risk, in violation of the metering guarantee), and
- Corrupt consensus-critical gas accounting (`GasUsed` feeding block gas limits, fee refunds, and receipts), risking state/consensus divergence between validators if the overflow condition is reached inconsistently.

However, this requires an extremely large `amount` argument to `ConsumeGas`/`RefundGas` (on the order of `MaxUint64/numerator`) or an attacker-influenced `multiplierNumerator` value; ordinary gas amounts used in Sei transactions (bounded by realistic block gas limits far below `2^64`) will not trigger the overflow under the currently configured `CosmosGasMultiplierNumerator/Denominator` values. I could not, within the available tool budget, fully confirm whether any reachable code path (e.g., a crafted CosmWasm/EVM call with pathological gas consumption bookkeeping, or a governance-set multiplier) can drive a single `ConsumeGas`/`RefundGas` call's `amount * numerator` product past `2^64`.

### Likelihood Explanation
Low-to-moderate under default parameters, because reaching the overflow requires gas amounts that are unrealistic for a single `ConsumeGas` call under real block-gas-limit constraints, and `CosmosGasMultiplierNumerator/Denominator` are governance/params-controlled rather than directly attacker-controlled per transaction. The bug is real (a genuine, unchecked integer-overflow multiplication precisely analogous to JBCrypt's unguarded key-stretching multiplication, sitting in a critical, universally-reachable ante-pipeline component) but exploitability by a single unprivileged transaction sender under default configuration is not confirmed.

### Recommendation
Add explicit overflow checking to `multiplierGasMeter.adjustGas`, mirroring the pattern already used in `basicGasMeter.ConsumeGas`/`infiniteGasMeter.ConsumeGas` (`addUint64Overflow`) and in `WasmGasRegister.ToWasmVMGas` (post-multiplication comparison), e.g.:
```go
func (g *multiplierGasMeter) adjustGas(original Gas) Gas {
    if g.multiplierNumerator != 0 && original > math.MaxUint64/g.multiplierNumerator {
        panic(ErrorGasOverflow{"multiplier gas overflow"})
    }
    return original * g.multiplierNumerator / g.multiplierDenominator
}
```

### Proof of Concept
Not independently confirmed as end-to-end exploitable given available context/time — the arithmetic overflow itself is directly demonstrable in isolation:
```go
// original = math.MaxUint64/2 + 1, multiplierNumerator = 3
// original * multiplierNumerator overflows uint64 and wraps to a small value
// before division by multiplierDenominator, so ConsumeGas under-charges gas
// for a given descriptor without tripping the out-of-gas panic.
```
Confirming a concrete reachable trigger from a single transaction (i.e., a call path that drives a single `ConsumeGas`/`RefundGas` invocation's `amount` to the required magnitude under real gas-limit/multiplier configuration) would require further investigation beyond what was possible in this session; a Devin session with full repo/build access is recommended to validate end-to-end reachability and craft a working PoC.

### Citations

**File:** sei-cosmos/store/types/gas.go (L98-113)
```go
func (g *basicGasMeter) ConsumeGas(amount Gas, descriptor string) {
	g.lock.Lock()
	defer g.lock.Unlock()

	var overflow bool
	g.consumed, overflow = addUint64Overflow(g.consumed, amount)
	if overflow {
		g.consumed = math.MaxUint64
		g.incrGasExceededCounter("overflow", descriptor)
		panic(ErrorGasOverflow{descriptor})
	}
	if g.consumed > g.limit {
		g.incrGasExceededCounter("out_of_gas", descriptor)
		panic(ErrorOutOfGas{descriptor})
	}
}
```

**File:** sei-cosmos/store/types/gas.go (L180-182)
```go
func (g *multiplierGasMeter) adjustGas(original Gas) Gas {
	return original * g.multiplierNumerator / g.multiplierDenominator
}
```

**File:** sei-cosmos/store/types/gas.go (L184-190)
```go
func (g *multiplierGasMeter) ConsumeGas(amount Gas, descriptor string) {
	g.basicGasMeter.ConsumeGas(g.adjustGas(amount), descriptor)
}

func (g *multiplierGasMeter) RefundGas(amount Gas, descriptor string) {
	g.basicGasMeter.RefundGas(g.adjustGas(amount), descriptor)
}
```

**File:** sei-cosmos/store/types/gas.go (L222-232)
```go
func (g *infiniteGasMeter) ConsumeGas(amount Gas, descriptor string) {
	g.lock.Lock()
	defer g.lock.Unlock()

	var overflow bool
	// TODO: Should we set the consumed field after overflow checking?
	g.consumed, overflow = addUint64Overflow(g.consumed, amount)
	if overflow {
		panic(ErrorGasOverflow{descriptor})
	}
}
```

**File:** sei-wasmd/x/wasm/keeper/gas_register.go (L215-222)
```go
// ToWasmVMGas convert to wasmVM contract runtime gas unit
func (g WasmGasRegister) ToWasmVMGas(source storetypes.Gas) uint64 {
	x := source * g.c.GasMultiplier
	if x < source {
		panic(sdk.ErrorOutOfGas{Descriptor: "overflow"})
	}
	return x
}
```

**File:** app/antedecorators/gas.go (L9-19)
```go
func GetGasMeterSetter(pk paramskeeper.Keeper) func(bool, sdk.Context, uint64, sdk.Tx) sdk.Context {
	return func(simulate bool, ctx sdk.Context, gasLimit uint64, tx sdk.Tx) sdk.Context {
		cosmosGasParams := pk.GetCosmosGasParams(ctx)

		// In simulation and genesis delivery, still use multiplier but with infinite gas limit.
		if simulate || ctx.IsGenesis() {
			return ctx.WithGasMeter(types.NewInfiniteMultiplierGasMeter(cosmosGasParams.CosmosGasMultiplierNumerator, cosmosGasParams.CosmosGasMultiplierDenominator))
		}

		return ctx.WithGasMeter(types.NewMultiplierGasMeter(gasLimit, cosmosGasParams.CosmosGasMultiplierNumerator, cosmosGasParams.CosmosGasMultiplierDenominator))
	}
```
