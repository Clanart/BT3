I found one concrete candidate in `getEvmGasLimitFromCtx`, where a raw `uint64` subtraction (`ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()`) is guarded, but let me verify the guard order and whether it can actually underflow before I commit to it.I found a concrete analog: an unchecked, non-SafeMath-style `uint64` subtraction that computes a gas quantity used to size a caller-facing gas value, guarded by a check performed *after* the subtraction already executed — mirroring the "unchecked `expiry - windowSize`" pattern from the report (subtract first, validate after/incompletely).

### Title
Unchecked `uint64` subtraction in EVM gas-limit derivation can underflow, corrupting gas accounting for CosmWasm→EVM and precompile calls - ([File: giga/deps/xevm/keeper/params.go])

### Summary
`getEvmGasLimitFromCtx` (and the near-identical `GetRemainingGas` helpers used throughout the precompile package) computes `ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()` as a raw `uint64` subtraction *before* checking whether the result is meaningful. Go's unsigned-integer subtraction does not panic and does not use a checked/"SafeMath"-style library — exactly the property that caused the original Convexity `expiry - windowSize` bug. If `GasConsumedToLimit()` can ever exceed `Limit()` at the point of subtraction, the result silently wraps to a huge `uint64` (`2**64 - Δ`), analogous to `expiry - windowSize` wrapping to `2**256 - 1`.

### Finding Description
```go
// giga/deps/xevm/keeper/params.go
func (k *Keeper) getEvmGasLimitFromCtx(ctx sdk.Context) uint64 {
	seiGasRemaining := ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()
	if ctx.GasMeter().Limit() <= 0 {
		return math.MaxUint64
	}
	...
}
``` [1](#0-0) 

The subtraction is evaluated unconditionally on line 253, and only *afterward* is `Limit() <= 0` checked as a special case; there is no check that `GasConsumedToLimit() <= Limit()` before the subtraction runs. `basicGasMeter.GasConsumedToLimit()` normally clamps `consumed` to `limit`, so under an ordinary `basicGasMeter` this subtraction cannot go negative [2](#0-1) . However, an `infiniteGasMeter` (used, e.g., for infinite/no-consumption contexts and CacheContext-style paths inside the ante/precompile pipeline) reports `Limit() == 0` and `GasConsumedToLimit()` equal to actual gas consumed (not clamped to 0) [3](#0-2) . In that situation `0 - consumed` underflows to `math.MaxUint64 - consumed + 1` before the subsequent `Limit() <= 0` guard even has a chance to correct the value — the guard replaces the *return value*, but the vulnerable pattern (subtract first, validate second) is structurally identical to the reported bug and is repeated verbatim in multiple other files that are reachable from live precompile execution:

```go
// precompiles/common/legacy/v555/precompiles.go, v562, v575 (all identical)
func GetRemainingGas(ctx sdk.Context, evmKeeper utils.EVMKeeper) uint64 {
	gasMultipler := evmKeeper.GetPriorityNormalizer(ctx)
	seiGasRemaining := ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()
	return sdk.NewDecFromInt(sdk.NewIntFromUint64(seiGasRemaining)).Quo(gasMultipler).TruncateInt().Uint64()
}
``` [4](#0-3) 

Unlike this file's twin in `giga/deps/xevm/keeper/params.go`, `GetRemainingGas` has **no** `Limit() <= 0` guard at all — if it is ever invoked against a context carrying an `infiniteGasMeter` (`Limit()==0`) with nonzero consumed gas, the subtraction underflows unconditionally and the wrapped `uint64` value is fed straight into `remainingGas`, which every precompile's `Execute` returns to the EVM's gas accounting (`vm.RunPrecompiledContract`) as the gas left after the call. This value directly participates in refund/gas-consumption bookkeeping for the calling EVM frame, so a huge bogus "remaining gas" value understates real consumption and lets the caller effectively get gas back it never had, or corrupts the deferred/ common gas metering shared across the tx.

### Impact Explanation
This is a fee/refund-accounting bug: if triggered, `remainingGas` reported by a precompile call becomes a near-`uint64`-max garbage value instead of a small positive number, which is then propagated into the EVM's own gas metering for the calling contract/transaction. Depending on how the caller (native EVM opcode dispatch) treats this returned value, this can result in gas being effectively refunded/never charged for real work performed inside the precompile call, i.e., fee/refund abuse reachable by any EVM contract calling one of Sei's precompiles (bank, staking, gov, wasmd, pointers, oracle, etc.) that route through `GetRemainingGas`/`getEvmGasLimitFromCtx`.

### Likelihood Explanation
I could **not conclusively prove** that a live, unprivileged-reachable code path drives `ctx.GasMeter()` to be an `infiniteGasMeter` (or any meter where `GasConsumedToLimit() > Limit()`) at the exact call sites of `GetRemainingGas`/`getEvmGasLimitFromCtx` during ordinary precompile execution — the `basicGasMeter`'s `GasConsumedToLimit()` clamp normally prevents the underflow, and I was not able to fully trace every gas-meter substitution (e.g., `ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))`, used adjacent to some of these call sites) through to these exact subtraction points within the available search budget. This is a real code-quality/robustness gap (missing an explicit "clamp to zero" or `SafeSub`-equivalent check before the subtraction, exactly the class of bug in the report) but I cannot confirm it is exploitable end-to-end without deeper tracing of gas-meter transitions across the ante pipeline and precompile dispatch, which would require live debugging/tests rather than static reading. Given this uncertainty, I present it as a plausible-but-unconfirmed analog rather than a fully proven vulnerability.

### Recommendation
Add an explicit `if consumed >= limit { return 0 }`-style guard (equivalent to `SafeSub`) before performing `Limit() - GasConsumedToLimit()` in both `getEvmGasLimitFromCtx` (giga/deps/xevm/keeper/params.go) and every duplicated `GetRemainingGas` implementation in `precompiles/common/**/precompiles.go`, rather than relying on a downstream `Limit() <= 0` special case that only covers one of the underflow conditions. Additionally, audit all gas-meter transition points (`ctx.WithGasMeter(...)`) that precede calls into these helpers to confirm no code path can present an `infiniteGasMeter`, or any other meter, in a state where `GasConsumedToLimit() > Limit()`.

### Proof of Concept
Not executable — this requires constructing a call path where the `sdk.Context` passed into `GetRemainingGas`/`getEvmGasLimitFromCtx` carries an `infiniteGasMeter` (or any `GasMeter` implementation returning `Limit()==0` with nonzero `GasConsumedToLimit()`) at the moment of the subtraction, which I was unable to fully confirm reachable from an unprivileged EVM precompile call within the scope of this review. A conclusive PoC would need a Devin session with test/debug access to trace gas-meter swaps through the ante handler → precompile dispatch → `RunAndCalculateGas` → `Execute` chain.

### Citations

**File:** giga/deps/xevm/keeper/params.go (L252-265)
```go
func (k *Keeper) getEvmGasLimitFromCtx(ctx sdk.Context) uint64 {
	seiGasRemaining := ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()
	if ctx.GasMeter().Limit() <= 0 {
		return math.MaxUint64
	}
	if ctx.ChainID() != Pacific1ChainID || ctx.BlockHeight() >= 119821526 {
		ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	}
	evmGasBig := sdk.NewDecFromInt(sdk.NewIntFromUint64(seiGasRemaining)).Quo(k.GetPriorityNormalizer(ctx)).TruncateInt().BigInt()
	if evmGasBig.Cmp(MaxUint64BigInt) > 0 {
		evmGasBig = MaxUint64BigInt
	}
	return evmGasBig.Uint64()
}
```

**File:** sei-cosmos/store/types/gas.go (L78-86)
```go
func (g *basicGasMeter) GasConsumedToLimit() Gas {
	g.lock.Lock()
	defer g.lock.Unlock()

	if g.consumed > g.limit {
		return g.limit
	}
	return g.consumed
}
```

**File:** sei-cosmos/store/types/gas.go (L206-220)
```go
}

func (g *infiniteGasMeter) GasConsumedToLimit() Gas {
	g.lock.Lock()
	defer g.lock.Unlock()

	return g.consumed
}

func (g *infiniteGasMeter) Limit() Gas {
	g.lock.Lock()
	defer g.lock.Unlock()

	return 0
}
```

**File:** precompiles/common/legacy/v562/precompiles.go (L241-245)
```go
func GetRemainingGas(ctx sdk.Context, evmKeeper putils.EVMKeeper) uint64 {
	gasMultipler := evmKeeper.GetPriorityNormalizer(ctx)
	seiGasRemaining := ctx.GasMeter().Limit() - ctx.GasMeter().GasConsumedToLimit()
	return sdk.NewDecFromInt(sdk.NewIntFromUint64(seiGasRemaining)).Quo(gasMultipler).TruncateInt().Uint64()
}
```
