### Title
Division-by-zero panic in KIP71 base fee calculation when `GasTarget` governance parameter is zero - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the `GasTarget` governance parameter without checking it for zero, unlike the adjacent `BaseFeeDenominator` field which the code explicitly guards against being zero.

### Finding Description
In `NextMagmaBlockBaseFee`, the code guards against `BaseFeeDenominator == 0` by substituting a fallback value of 64 (`params/kip71_config.go:70-76`), with a comment "To avoid panic, set the fluctuation range small" — showing the developers were aware that a zero divisor would panic. However, `gasTarget := kc.GasTarget` (line 77) receives no equivalent check.

Later, when `parentGasUsed != gasTarget`, the code computes:
```go
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
```
at both `params/kip71_config.go:102` (gas-used-above-target branch) and `params/kip71_config.go:120` (gas-used-below-target branch). `math/big.Int.Div` panics when the divisor is zero. If `GasTarget` is ever set to `0` via the KIP-71/Magma governance parameter set, and any block has `parentGasUsed != 0` (i.e., any transaction consumed gas, since `gasTarget == 0` almost guarantees `parentGasUsed > gasTarget`), every node computing the next base fee — during header verification in `VerifyMagmaHeader` (`params/kip71_config.go:45-56`) or in RPC-facing paths such as `node/cn/gasprice/feehistory.go` (`kip71Config.NextMagmaBlockBaseFee(...)` at line 112) — will panic.

This mirrors the CVE-2021-42391 bug class: an unchecked, externally influenced value used as a divisor, causing a divide-by-zero crash, while an adjacent, structurally identical value (`BaseFeeDenominator`) was fixed but `GasTarget` was missed.

### Impact Explanation
A zero divisor in `big.Int.Div` triggers a Go runtime panic, crashing every full node that evaluates `NextMagmaBlockBaseFee` for header verification or fee estimation once the parameter takes effect. Because base-fee verification runs on every block for every syncing/validating node (`VerifyMagmaHeader`), this is a network-wide denial-of-service that halts block processing across all honest nodes simultaneously — a stronger outcome than typical "resource-only" DoS since it is a deterministic consensus-path crash, not just resource exhaustion. Public RPC servers calling `eth_feeHistory` (which invokes `processBlock`/`NextMagmaBlockBaseFee`) would likewise crash on any historical block sealed with `GasTarget == 0`.

### Likelihood Explanation
This requires `KIP71Config.GasTarget` to become `0`, which is set via the KIP-71/Magma governance parameter mechanism (`kaiax/gov/param.go`, `kaiax/gov/paramset.go`). I was not able to confirm within the available index whether governance vote validation rejects a `GasTarget = 0` proposal before it is written into `headergov` state (the analogous zero-check exists only for `BaseFeeDenominator` inside `kip71_config.go`, not at the governance-vote validation layer for `GasTarget`). If governance vote validation does not explicitly reject `gastarget=0`, a single governance vote (by any party with vote-casting rights in the governance module) sealed into a block header would be sufficient to trigger the crash on the very next block with nonzero gas usage. This significantly limits confidence versus a bug reachable purely from an unprivileged transaction; it depends on governance-parameter admission logic that I could not fully verify with available tools.

### Recommendation
Add the same zero-guard used for `BaseFeeDenominator` to `GasTarget` in `NextMagmaBlockBaseFee` (substitute a safe default, or reject/clamp the value at governance-parameter validation time in `kaiax/gov/param.go`/`paramset.go`), so that `GasTarget == 0` can never reach `big.Int.Div` as a divisor.

### Proof of Concept
1. Have the governance parameter `governance.kip71.gastarget` set to `0` (via a governance vote sealed in a block header, assuming — unverified — that current vote validation does not reject this value).
2. Once the parameter takes effect, produce/observe any block whose `GasUsed > 0`.
3. When any node computes `NextMagmaBlockBaseFee` for the following block (during header verification, or via `eth_feeHistory`), `parentGasUsed (>0) != gasTarget (0)` takes the `parentGasUsed > gasTarget` branch, executing `x.Div(x, new(big.Int).SetUint64(0))`, which panics and crashes the node process. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
```

**File:** params/kip71_config.go (L70-76)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
```

**File:** params/kip71_config.go (L88-103)
```go
	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

**File:** node/cn/gasprice/feehistory.go (L105-115)
```go
		pset        = oracle.govModule.GetParamSet(bf.blockNumber + 1)
		kip71Config = pset.ToKip71Config()
	)
	if bf.results.baseFee = bf.header.BaseFee; bf.results.baseFee == nil {
		bf.results.baseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
