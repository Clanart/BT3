### Title
Deterministic Chain Halt: `CalculateBaseFee` Panics on Valid CometBFT `max_gas=-1` Configuration — (`x/feemarket/keeper/eip1559.go`)

---

### Summary

`BlockGasLimit` and `CalculateBaseFee` both read `ctx.ConsensusParams().Block.MaxGas` but apply irreconcilable transformations to the value `-1`. `BlockGasLimit` explicitly documents and handles `-1` as "unlimited" (mapping it to `math.MaxUint64`), while `CalculateBaseFee` unconditionally panics on the same value. Because `CalculateBaseFee` is called in `BeginBlock`, any chain configured with `max_gas=-1` and EIP-1559 enabled will deterministically halt on every block after `EnableHeight`.

---

### Finding Description

**`types/block.go` — `BlockGasLimit`:**

`BlockGasLimit` explicitly documents `-1` as a valid CometBFT value meaning "no limit" and maps it to `math.MaxUint64`: [1](#0-0) 

**`x/feemarket/keeper/eip1559.go` — `CalculateBaseFee`:**

`CalculateBaseFee` reads the same field and panics when it is `-1`: [2](#0-1) 

The condition `consParams.Block.MaxGas <= -1` is true for the only valid "unlimited" sentinel value. The panic is unconditional — there is no fallback or graceful return.

**`x/feemarket/keeper/abci.go` — `BeginBlock`:**

`CalculateBaseFee` is called directly in `BeginBlock`: [3](#0-2) 

A panic in `BeginBlock` is fatal to the node process. Because all validators execute the same deterministic code path, every validator panics on the same block, causing a full chain halt.

**Activation condition — `IsBaseFeeEnabled`:**

EIP-1559 is active when `!p.NoBaseFee && height >= p.EnableHeight`. With default parameters, `DefaultNoBaseFee = false` and `DefaultEnableHeight = 0`: [4](#0-3) 

This means EIP-1559 is enabled by default from block 0. The only block that escapes the panic is exactly `EnableHeight` itself, because `CalculateBaseFee` returns early there: [5](#0-4) 

Every subsequent block hits the panic.

---

### Impact Explanation

This is a **deterministic chain halt**. No attacker action is required — the halt is triggered by the chain's own `BeginBlock` execution. Any chain that:
1. Sets `max_gas = -1` in CometBFT consensus params (a valid, documented configuration meaning "unlimited gas"), and
2. Has EIP-1559 enabled (the default)

...will halt permanently after `EnableHeight`. All validators hit the same panic simultaneously, making recovery impossible without a coordinated emergency upgrade or parameter change via governance (which itself requires blocks to be produced).

Impact category: **Critical — block-processing path causes deterministic validator consensus failure / chain halt.**

---

### Likelihood Explanation

`max_gas = -1` is a first-class, documented CometBFT configuration. Chains that want unlimited block gas (common in development, testnets, and some production chains that rely on per-tx gas limits instead) will use this value. The Ethermint codebase itself acknowledges this in `BlockGasLimit`'s comment. The combination of default EIP-1559 parameters and this CometBFT setting is entirely plausible in production deployments.

---

### Recommendation

Replace the panic in `CalculateBaseFee` with the same handling used in `BlockGasLimit`. When `MaxGas == -1`, either:
- Return `nil` (skip base fee recalculation for this block, treating it as unlimited), or
- Use a well-defined large sentinel value consistent with `BlockGasLimit`'s `math.MaxUint64` mapping.

The fix should be applied at: [6](#0-5) 

A shared helper (e.g., in `types/block.go`) should be used by both `BlockGasLimit` and `CalculateBaseFee` to ensure the invariant is maintained in one place.

---

### Proof of Concept

**Invariant violated:** For all valid `int64` values of `ConsensusParams.Block.MaxGas` (i.e., `-1` and any non-negative value), `BlockGasLimit` and `CalculateBaseFee` must agree on whether the value is valid.

**Concrete trigger sequence:**
1. Configure chain with `consensus_params.block.max_gas = -1`
2. Deploy with default feemarket params (`NoBaseFee=false`, `EnableHeight=0`)
3. Start chain — block 1 is produced
4. `BeginBlock` → `CalculateBaseFee` → `consParams.Block.MaxGas` is `-1` → condition `<= -1` is true → `panic(...)` → node crashes
5. All validators crash on block 1 → chain halted

No transaction submission, contract call, or privileged access is required. The halt is triggered purely by normal block processing against a valid chain configuration.

### Citations

**File:** types/block.go (L36-40)
```go
	// Setting max_gas to -1 in CometBFT means there is no limit on the maximum gas consumption for transactions
	// https://github.com/cometbft/cometbft/blob/v0.37.2/proto/tendermint/types/params.proto#L25-L27
	if maxGas == -1 {
		return math.MaxUint64
	}
```

**File:** x/feemarket/keeper/eip1559.go (L44-46)
```go
	if ctx.BlockHeight() == params.EnableHeight {
		return params.BaseFee.BigInt()
	}
```

**File:** x/feemarket/keeper/eip1559.go (L59-63)
```go
	// NOTE: a MaxGas equal to -1 means that block gas is unlimited
	if consParams.Block == nil || consParams.Block.MaxGas <= -1 {
		panic(fmt.Sprintf("get invalid consensus params: %s", consParams))
	}
	gasLimit := big.NewInt(consParams.Block.MaxGas)
```

**File:** x/feemarket/keeper/abci.go (L30-32)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

```

**File:** x/feemarket/types/params.go (L135-137)
```go
func (p Params) IsBaseFeeEnabled(height int64) bool {
	return !p.NoBaseFee && height >= p.EnableHeight
}
```
