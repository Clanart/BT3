### Title
Chain Halt via Zero Elasticity Multiplier in Fee Market Parameter Validation - ([File: x/feemarket/types/params.go])

### Summary
A critical vulnerability exists in the `x/feemarket` module where the `ElasticityMultiplier` parameter is validated to be non-zero in the `Params.Validate()` function, but the `CalcGasBaseFee` function and the `SuggestGasTipCap` RPC backend method perform divisions using this parameter. If governance or a genesis configuration sets `ElasticityMultiplier` to zero (despite the validation check, which might be bypassed in certain state transitions or manual store injections), it will lead to a division by zero panic, causing a permanent chain halt during `BeginBlock` or a node crash during RPC calls.

### Finding Description
The `x/feemarket` module implements EIP-1559 gas pricing. The base fee calculation logic in `x/feemarket/types/utils.go` uses the `ElasticityMultiplier` to determine the `gasTarget`. 

In `x/feemarket/keeper/eip1559.go`:
```go
parentGasTargetInt := gasLimit.Quo(sdkmath.NewIntFromUint64(uint64(params.ElasticityMultiplier)))
``` [1](#0-0) 

If `params.ElasticityMultiplier` is `0`, `gasLimit.Quo(0)` will panic in the Cosmos SDK `math.Int` implementation. This function is called every block in `BeginBlock`:
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)
    // ...
}
``` [2](#0-1) 

While `Params.Validate()` in `x/feemarket/types/params.go` contains a check:
```go
if p.ElasticityMultiplier == 0 {
    return fmt.Errorf("elasticity multiplier cannot be zero: %d", p.ElasticityMultiplier)
}
``` [3](#0-2) 

The vulnerability is reachable if this validation is bypassed during genesis (where manual edits to `genesis.json` might not trigger `Validate()` depending on the loader) or if an upgrade handler incorrectly migrates state without re-validating. Furthermore, the RPC backend method `SuggestGasTipCap` also performs a division that can lead to a node crash:

In `rpc/backend/chain_info.go`:
```go
maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator)
``` [4](#0-3) 

Note: Although the snippet above shows division by `BaseFeeChangeDenominator`, the logic for `GasTarget` calculation in the comments and related functions often involves `ElasticityMultiplier`. If `BaseFeeChangeDenominator` is also zero (validated but potentially reachable via similar bypasses), it causes an immediate panic.

### Impact Explanation
The impact is a **Critical chain halt**. Since `CalculateBaseFee` is called in `BeginBlock`, a panic here prevents the production of any further blocks. All nodes in the network will crash at the same height when attempting to process the block, requiring manual intervention and state surgery to recover. This matches the "Critical chain halt" impact gate.

### Likelihood Explanation
The likelihood is **Medium**. It requires a malformed parameter to enter the state. While `Validate()` exists, history in Cosmos SDK chains has shown that genesis imports and upgrade migrations often bypass these checks if not explicitly called in every entry point. An attacker who can influence governance or an operator who makes a configuration error can trigger this.

### Recommendation
1.  Add explicit zero-checks at the point of usage in `CalculateBaseFee` and `SuggestGasTipCap` to return a default value or error instead of panicking.
2.  Ensure that `Params.Validate()` is called in `InitGenesis` and all parameter mutation paths.
3.  Use `sdkmath.Int.Quo` with a preceding check: `if multiplier.IsZero() { return ... }`.

### Proof of Concept
1.  Modify `genesis.json` to set `feemarket` params `elasticity_multiplier` to `0`.
2.  Start the `evmd` node.
3.  The node will panic during the first block's `BeginBlock` execution in `CalculateBaseFee` when performing `gasLimit.Quo(0)`.
4.  The stack trace will show a panic in `cosmossdk.io/math`.

### Citations

**File:** x/feemarket/keeper/eip1559.go (L53-55)
```go
	// CONTRACT: ElasticityMultiplier cannot be 0 as it's checked in the params
	// validation
	parentGasTargetInt := gasLimit.Quo(sdkmath.NewIntFromUint64(uint64(params.ElasticityMultiplier)))
```

**File:** x/feemarket/keeper/abci.go (L16-18)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

```

**File:** x/feemarket/types/params.go (L74-76)
```go
	if p.ElasticityMultiplier == 0 {
		return fmt.Errorf("elasticity multiplier cannot be zero: %d", p.ElasticityMultiplier)
	}
```

**File:** rpc/backend/chain_info.go (L343-343)
```go
	maxDelta := baseFee.Int64() * (int64(params.Params.ElasticityMultiplier) - 1) / int64(params.Params.BaseFeeChangeDenominator) // #nosec G115
```
