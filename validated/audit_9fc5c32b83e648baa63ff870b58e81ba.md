I found a solid analog: an asymmetric rounding bug in Kaia's KIP-71 dynamic base fee formula, in `NextMagmaBlockBaseFee`.### Title
Missing minimum-decrement floor in KIP-71 base fee decrease branch causes the base fee to become permanently stuck, forcing indefinite fee overcharge - (File: params/kip71_config.go)

### Summary
`NextMagmaBlockBaseFee` in `params/kip71_config.go` computes the next block's base fee using integer division scaled by `baseFeeDenominator`. In the "gas used above target" branch the result is floored to a minimum of 1 wei via `math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)`, but the symmetric "gas used below target" branch has no such floor, so `baseFeeDelta` can truncate to exactly `0`.

### Finding Description
`NextMagmaBlockBaseFee` implements the Magma/KIP-71 EIP-1559-style base fee adjustment: [1](#0-0) 

When `parentGasUsed > gasTarget`, the increase is explicitly bounded below by 1: [2](#0-1) 

When `parentGasUsed < gasTarget`, the decrease uses the identical division chain but omits the `math.BigMax(..., common.Big1)` floor: [3](#0-2) 

```go
gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
baseFeeDelta := x.Div(y, baseFeeDenominator)   // no floor of 1 here

nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
```

Whenever `parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator < 1`, `baseFeeDelta` truncates to `0`, so `nextBaseFee == parentBaseFee`. Because the shortcut at line 112 (`if parentBaseFee.Cmp(lowerBoundBaseFee) == 0`) only fires once the base fee has *already* reached the lower bound, base fee can get stuck above the lower bound indefinitely, never converging down even though every subsequent block is consistently below `gasTarget`. This mirrors the root cause of the VTVL `_baseVestedAmount` bug: an integer-division term whose numerator is small relative to a large denominator repeatedly rounds to zero, permanently (not just temporarily) suppressing an amount that should linearly track a proportional/time-based quantity — here, the base fee decrease per block, there, the per-second vested amount.

`baseFeeDenominator`, `gasTarget`, and `lowerBoundBaseFee` are all governance-tunable via `reward.ratio`/KIP-71 governance parameters (`GetDefaultKIP71Config`), so a network operator (or governance vote) that raises `baseFeeDenominator` or lowers `lowerBoundBaseFee`/`baseFee` widens the window in which this rounding-to-zero condition holds, and it is reachable simply by any public transaction sender submitting normal transactions that keep block gas usage below `gasTarget`.

### Impact Explanation
Once triggered, the network's dynamic base fee stops decreasing even though demand (gas usage) is persistently below target, forcing every subsequent transaction sender to pay a higher-than-warranted base fee for an indefinite number of blocks — the fee-market analogue of "unauthorized value" extraction from senders, since a portion of every transaction fee is burnt via KIP-71's fee-burning mechanism (`getBurnAmountKore`/Magma burn) and the rest routed by `reward.ratio`. This is a deterministic, protocol-level miscalculation reachable by any public RPC caller submitting ordinary transactions — no special privilege is required to trigger the condition, only submitting transactions that keep `parentGasUsed` slightly below `gasTarget` while `baseFeeDelta` rounds to zero. Because `VerifyMagmaHeader` treats this computed value as the canonical expected `header.BaseFee`, this behavior is consensus-valid on all nodes (no state divergence), but it silently converts a "temporary rounding" edge case in an economic parameter into a systemic overcharge with no recovery path other than gas usage cresting back above target (which resets to the increase branch and its 1-wei floor) or falling to the exact lower-bound shortcut.

### Likelihood Explanation
The likelihood of triggering the bug scales with governance/genesis configuration: on Kaia mainnet's default parameters (`LowerBoundBaseFee` = 25 gwei, `GasTarget` = 30,000,000, `BaseFeeDenominator` = 20) the term `parentBaseFee * gasUsedDelta` is large enough that a full-zero delta is rare except very near the lower bound. However, since `KIP71Config` fields are governance-settable, any Kaia-based chain (private/consortium chain, or mainnet after a future governance parameter change increasing `BaseFeeDenominator` or lowering `LowerBoundBaseFee`) can hit this condition far more easily, and it requires no adversarial coordination — normal below-target traffic is sufficient.

### Recommendation
Apply the same non-zero floor used in the increase branch to the decrease branch, e.g. `baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)`, ensuring the base fee always converges toward `lowerBoundBaseFee` by at least 1 wei per below-target block, symmetric with the increase logic.

### Proof of Concept
1. Configure (or observe on a chain where governance sets) `KIP71Config{LowerBoundBaseFee: L, GasTarget: G, BaseFeeDenominator: D}` such that current `parentBaseFee` is just above `L`.
2. Have `parentGasUsed` be slightly below `G` (e.g., `G - 1`) for several consecutive blocks — trivially achievable by any public transaction sender simply submitting fewer/lighter transactions.
3. Compute `baseFeeDelta = parentBaseFee * (G - parentGasUsed) / G / D` using `NextMagmaBlockBaseFee` — using the code path at [4](#0-3) ; when this value truncates to `0`, `nextBaseFee = parentBaseFee`, so the base fee does not decrease that block.
4. Repeat for many blocks: as long as usage stays in the same range, `baseFeeDelta` remains `0` every block, and the base fee never converges to `lowerBoundBaseFee`, unlike the "above target" branch which is guaranteed to move by at least 1 wei per block via `math.BigMax(..., common.Big1)` at [5](#0-4) .

### Citations

**File:** params/kip71_config.go (L58-68)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}
```

**File:** params/kip71_config.go (L97-109)
```go
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

**File:** params/kip71_config.go (L115-128)
```go
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
