### Division by zero in EIP-1559 base fee calculation - ([File: x/feemarket/types/utils.go](x/feemarket/types/utils.go))

### Summary
The `x/feemarket` module's base fee calculation logic contains a potential division by zero vulnerability in `CalcGasBaseFee`. While the module's parameters are validated to prevent `ElasticityMultiplier` from being zero, a zero `gasLimit` (derived from consensus parameters `MaxGas`) can result in a `gasTarget` of zero. If `gasTarget` is zero and `gasUsed` is non-zero, the function will panic due to division by zero, leading to a chain halt.

### Finding Description
In the `x/feemarket` module, the `CalculateBaseFee` function computes the next block's base fee based on the parent block's gas usage and target. The `gasTarget` is calculated as `gasLimit / ElasticityMultiplier` [1](#0-0) . The `gasLimit` is retrieved from the consensus parameters `MaxGas` [2](#0-1) .

The calculated `gasTarget` is passed to `CalcGasBaseFee` [3](#0-2) . Inside `CalcGasBaseFee`, the code performs a division by `gasTarget`:
```go
num = num.QuoInt(math.NewIntFromUint64(gasTarget))
``` [4](#0-3) 

Although there is a check `if gasTarget == 0 { return math.LegacyZeroDec() }` [5](#0-4) , this check is preceded by an equality check:
```go
if gasUsed == gasTarget {
    return baseFee
}
``` [6](#0-5) 

If both `gasUsed` and `gasTarget` are zero, the function returns safely. However, if `gasLimit` is set to 0 (e.g., via governance or misconfiguration), `gasTarget` becomes 0. If any gas was consumed in the block (`gasUsed > 0`), the execution proceeds past the equality check and hits the division by zero in `QuoInt` if the `gasTarget == 0` check is not comprehensive or if the `gasUsed > gasTarget` logic is reached. Specifically, if `gasUsed > 0` and `gasTarget == 0`, the subtraction `gasUsed - gasTarget` [7](#0-6)  results in a non-zero `num`, which is then divided by `gasTarget` (0) [4](#0-3) , causing a panic.

### Impact Explanation
A panic in `BeginBlock` (where `CalculateBaseFee` is called) [8](#0-7)  is a non-recoverable error in the Cosmos SDK. If a block is produced where `gasUsed > 0` but `gasTarget` is 0, every node attempting to process this block will panic. This results in a permanent chain halt and consensus failure, requiring manual intervention or a hard fork to resolve.

### Likelihood Explanation
The likelihood is medium-low as it requires the consensus parameter `MaxGas` to be set to 0 while transactions are still being processed or if the `gasLimit` logic fails to default to a safe value. However, since this can be triggered by a state transition (consensus parameter change), it poses a critical risk to chain stability.

### Recommendation
Ensure that `gasTarget` is never zero before performing division. The check `if gasTarget == 0` should be moved to the very beginning of the `CalcGasBaseFee` function, or the `CalculateBaseFee` keeper method should enforce a minimum `gasTarget` of 1.

### Proof of Concept
1. Set the consensus parameter `Block.MaxGas` to `0`.
2. The `Keeper.CalculateBaseFee` will calculate `parentGasTargetInt = 0 / ElasticityMultiplier = 0` [1](#0-0) .
3. If a transaction is included in the block (e.g., a high-priority tx that bypasses gas checks or a system tx), `parentGasUsed` will be `> 0`.
4. `CalcGasBaseFee` is called with `gasUsed > 0` and `gasTarget = 0`.
5. The check `if gasUsed == gasTarget` (0 == 0) fails because `gasUsed > 0`.
6. The execution reaches `num.QuoInt(math.NewIntFromUint64(gasTarget))` [4](#0-3)  where `gasTarget` is 0.
7. The application panics and the chain halts.

### Citations

**File:** x/feemarket/keeper/eip1559.go (L50-50)
```go
		gasLimit = sdkmath.NewInt(consParams.Block.MaxGas)
```

**File:** x/feemarket/keeper/eip1559.go (L55-55)
```go
	parentGasTargetInt := gasLimit.Quo(sdkmath.NewIntFromUint64(uint64(params.ElasticityMultiplier)))
```

**File:** x/feemarket/keeper/eip1559.go (L63-63)
```go
		parentGasTargetInt.Uint64(),
```

**File:** x/feemarket/types/utils.go (L7-9)
```go
	if gasUsed == gasTarget {
		return baseFee
	}
```

**File:** x/feemarket/types/utils.go (L11-13)
```go
	if gasTarget == 0 {
		return math.LegacyZeroDec()
	}
```

**File:** x/feemarket/types/utils.go (L15-15)
```go
	num := math.LegacyNewDecFromInt(math.NewIntFromUint64(gasUsed).Sub(math.NewIntFromUint64(gasTarget)).Abs())
```

**File:** x/feemarket/types/utils.go (L17-17)
```go
	num = num.QuoInt(math.NewIntFromUint64(gasTarget))
```

**File:** x/feemarket/keeper/abci.go (L17-17)
```go
	baseFee := k.CalculateBaseFee(ctx)
```
