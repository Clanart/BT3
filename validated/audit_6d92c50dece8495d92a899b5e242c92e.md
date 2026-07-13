The vulnerability is real and concrete. Here is the analysis:

---

### Title
`CalculateBaseFee` panics on valid CometBFT `max_gas=-1` configuration, causing deterministic chain halt when EIP-1559 is enabled — (`x/feemarket/keeper/eip1559.go`)

### Summary

`BlockGasLimit` and `CalculateBaseFee` both read `ctx.ConsensusParams().Block.MaxGas` but apply irreconcilable transformations to the value `-1`. `BlockGasLimit` correctly maps `-1` to `math.MaxUint64` per the CometBFT spec. `CalculateBaseFee` unconditionally panics on `-1`. Since `CalculateBaseFee` is called from `BeginBlock` with no panic recovery, any chain configured with `max_gas=-1` and EIP-1559 enabled will halt on every block after `EnableHeight`.

### Finding Description

**`BlockGasLimit` (`types/block.go`)** explicitly documents and handles the `-1` case: [1](#0-0) 

```go
// Setting max_gas to -1 in CometBFT means there is no limit...
if maxGas == -1 {
    return math.MaxUint64
}
```

**`CalculateBaseFee` (`x/feemarket/keeper/eip1559.go`)** treats the same value as a fatal error: [2](#0-1) 

```go
// NOTE: a MaxGas equal to -1 means that block gas is unlimited
if consParams.Block == nil || consParams.Block.MaxGas <= -1 {
    panic(fmt.Sprintf("get invalid consensus params: %s", consParams))
}
```

The comment on line 59 even acknowledges that `-1` means "unlimited" — yet the very next line panics on it. This is a self-contradicting invariant in the same function.

**`BeginBlock` (`x/feemarket/keeper/abci.go`)** calls `CalculateBaseFee` with no panic recovery: [3](#0-2) 

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    if baseFee == nil {
        return nil
    }
    ...
```

The `panic` in `CalculateBaseFee` propagates through `BeginBlock` uncaught, crashing the node process and halting the chain.

### Impact Explanation

Any chain that:
1. Sets `max_gas=-1` in CometBFT consensus params (a documented, valid, and commonly used "unlimited" configuration), and
2. Has EIP-1559 enabled (`NoBaseFee=false` and `BlockHeight >= EnableHeight`)

...will panic on **every single block** after `EnableHeight`. The panic is deterministic across all validators, so the entire chain halts. No transaction submission or attacker action is required — the halt is triggered purely by block processing.

This matches the allowed impact: **"Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain."**

### Likelihood Explanation

`max_gas=-1` is a standard CometBFT configuration for chains that want unlimited block gas (relying on EVM gas limits alone). Chains migrating to EIP-1559 while running with this configuration will hit the panic the moment `EnableHeight` is reached. The preconditions are entirely within normal operational parameters.

### Recommendation

Replace the `panic` in `CalculateBaseFee` with the same logic used in `BlockGasLimit`: treat `MaxGas == -1` as unlimited and either return `nil` (disabling the EIP-1559 adjustment) or use a sentinel large value. The comment on line 59 already documents the correct semantic — the implementation must match it. [2](#0-1) 

### Proof of Concept

Invariant test (conceptual):
```
For all valid int64 max_gas values in CometBFT:
  - max_gas = -1 → BlockGasLimit returns math.MaxUint64 (valid)
  - max_gas = -1 → CalculateBaseFee panics (INVALID — chain halt)
```

Concrete trigger sequence:
1. Configure chain with `consensus_params.block.max_gas = -1`
2. Set feemarket params: `NoBaseFee=false`, `EnableHeight=N`
3. Produce block at height `N+1`
4. `BeginBlock` → `CalculateBaseFee` → `panic` → chain halt

### Citations

**File:** types/block.go (L36-40)
```go
	// Setting max_gas to -1 in CometBFT means there is no limit on the maximum gas consumption for transactions
	// https://github.com/cometbft/cometbft/blob/v0.37.2/proto/tendermint/types/params.proto#L25-L27
	if maxGas == -1 {
		return math.MaxUint64
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

**File:** x/feemarket/keeper/abci.go (L30-36)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

	// return immediately if base fee is nil
	if baseFee == nil {
		return nil
	}
```
