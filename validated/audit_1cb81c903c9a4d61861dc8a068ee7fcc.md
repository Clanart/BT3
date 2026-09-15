### Title
KIP-71 base-fee decrease formula can silently freeze the base fee at an elevated value due to unguarded integer truncation - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` (Magma/KIP-71 dynamic base-fee formula) computes `baseFeeDelta` differently for the increase path and the decrease path. The increase path is protected by `math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)`, guaranteeing the base fee always moves by at least 1 unit when usage exceeds the target. The decrease path has no equivalent floor: `baseFeeDelta := x.Div(y, baseFeeDenominator)` can legitimately evaluate to `0` through integer-division truncation, which permanently prevents the base fee from ever falling back toward the lower bound as long as usage stays below target by a similar margin every block.

### Finding Description [1](#0-0) 
On the "usage above target" branch, the delta is floored at 1:
```
baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
``` [2](#0-1) 
On the "usage below target" branch there is no such floor:
```
gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
baseFeeDelta := x.Div(y, baseFeeDenominator)

nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
```
If `y < baseFeeDenominator`, `baseFeeDelta` truncates to `0`, and `nextBaseFee == parentBaseFee`: the base fee simply repeats forever, never converging toward `LowerBoundBaseFee`, as long as every subsequent block's usage keeps `y` below `baseFeeDenominator`. This is precisely the `_collateralRatioRecoveryDuration`/`_maxCollateralRatioMantissa` bug class from the referenced Surge report — a linear "recovery" adjustment (`change = delta * numerator / denominator`) whose governance-controlled parameters (`GasTarget`, `BaseFeeDenominator`, bounds) can make the per-step change always round down to zero, freezing an on-chain price/ratio mechanism that should adapt over time.

`GasTarget` and `BaseFeeDenominator` are set via header governance votes (`kaiax/gov`) with only loose format checks (no upper bound enforced) — see the parameter table referenced in `kaiax/gov/param.go`/`kaiax/gov/headergov/README.md`, which validates `RewardRatio`-type strings but places no numeric ceiling on `basefeedenominator` or `gastarget` themselves. There is no cross-validation ensuring `BaseFeeDenominator` stays small enough relative to `GasTarget` and typical `parentBaseFee` to avoid this truncation, unlike the fix Surge applied requiring `_collateralRatioFallDuration`/`_collateralRatioRecoveryDuration < _maxCollateralRatioMantissa`.

### Impact Explanation
When this asymmetry is triggered (via a governance-set `BaseFeeDenominator` that is large relative to `GasTarget * parentBaseFee` scaling, or via network usage that persistently sits just below `GasTarget`), the base fee can get stuck above its true market-clearing/lower-bound value indefinitely. Every subsequent transaction sender is forced to pay this artificially inflated `BaseFee` (or `BaseFee`-derived `SuggestPrice`/`SuggestTipCap` via `node/cn/gasprice`) even though usage indicates fees should be falling. This is a persistent, protocol-level fee-overcharge affecting every public-RPC caller and transaction sender on the chain, and a permanent divergence from the intended KIP-71 pricing behavior (state divergence from the documented/intended dynamic fee schedule). Because the increase side is explicitly protected against the symmetric issue (with `math.BigMax(..., common.Big1)`) but the decrease side is not, this looks like an unintentional omission rather than a deliberate design choice.

### Likelihood Explanation
Reaching the truncation condition requires no special privilege beyond normal governance parameter-setting (in scope per this review's "governance parameters"/"KIP-71 pricing" area) and ordinary block gas usage patterns; no malicious validator collusion or peer manipulation is required — it manifests purely from the arithmetic in `NextMagmaBlockBaseFee`, which runs on every block once Magma is active and is deterministically verified in `VerifyMagmaHeader`. The likelihood of the exact numeric window being hit depends on the chosen `BaseFeeDenominator`/`GasTarget`/`parentBaseFee` combination and how close usage stays to target, so it is parameter-dependent, mirroring the Surge finding's own caveat about "certain parameter choices."

### Recommendation
Apply the same floor used on the increase path to the decrease path, e.g.:
```go
baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
```
guarded so it cannot decrease below `lowerBoundBaseFee` (already checked below). Additionally, consider adding governance-level format/consistency checks on `basefeedenominator` and `gastarget` (e.g., enforce `BaseFeeDenominator` stays within a sane bound relative to `GasTarget` and the base fee bounds) similar to the Surge fix constraining `_collateralRatioFallDuration`/`_collateralRatioRecoveryDuration` against `_maxCollateralRatioMantissa`.

### Proof of Concept
1. Governance sets (or genesis configures) `KIP71Config{GasTarget: G, BaseFeeDenominator: D, LowerBoundBaseFee: L, UpperBoundBaseFee: U}` such that for the current `parentBaseFee = B` and a plausible `parentGasUsed = G - Δ` (Δ small relative to `G`), `y = B*Δ/G < D`.
2. Any block producer/network activity naturally produces `parentGasUsed` in that below-target range each block (no special manipulation needed beyond normal light usage).
3. Call `NextMagmaBlockBaseFee(parentHeaderNumber, B, G-Δ)` repeatedly (as `params/kip71_config_test.go`'s `blocksToReachExpectedBaseFee` test harness does) — observe `nextBaseFee == B` every iteration, i.e., the loop never terminates/reaches `LowerBoundBaseFee`, unlike the corresponding increase-side test which always converges because of the `common.Big1` floor.
4. This can be directly reproduced by extending `TestBlocksToReachExpectedBaseFee` in `params/kip71_config_test.go` with a denominator/gasTarget/baseFee combination satisfying the inequality above and asserting the loop fails to terminate (or terminates at an unexpectedly huge block count) when descending toward the lower bound, contrasted with the ascending case which always terminates due to the `common.Big1` floor.

### Citations

**File:** params/kip71_config.go (L92-109)
```go
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

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
```

**File:** params/kip71_config.go (L110-128)
```go
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```
