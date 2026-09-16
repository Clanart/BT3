### Title
Division-by-zero panic in KIP-71 base fee calculation when governance sets `GasTarget` to 0 - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the governance-controlled `GasTarget` parameter without checking for zero, unlike the adjacent `BaseFeeDenominator` field which is explicitly guarded. This is the same bug class as CVE-2018-19628 (an integer field consumed as a divisor without a zero-check leads to a crash), applied to Kaia's Magma/KIP-71 base-fee formula instead of Wireshark's ZigBee dissector.

### Finding Description
`NextMagmaBlockBaseFee` computes the delta to apply to the base fee using `gasTarget` as a divisor in both the "gas used above target" and "gas used below target" branches: [1](#0-0) [2](#0-1) 

Unlike `BaseFeeDenominator`, which has an explicit `if kc.BaseFeeDenominator == 0 { ... }` fallback at [3](#0-2) , there is no equivalent guard for `gasTarget := kc.GasTarget` at [4](#0-3) . If `GasTarget == 0` and the block's gas used is non-zero, execution takes the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which panics in Go's `math/big` package.

Critically, the governance parameter validator for this field, `Kip71GasTarget`, uses `noopFormatChecker` (i.e., no format validation), in contrast to `Kip71BaseFeeDenominator` which explicitly rejects zero: [5](#0-4) . This means a governance vote (or genesis/hardfork config) can set `GasTarget` to `0` and it will pass validation.

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked from multiple hot paths that every node executes when processing blocks and transactions, including block header verification (`blockchain/block_validator.go`), the transaction pool (`blockchain/tx_pool.go`), block production (`work/worker.go`), and RPC gas price/fee-history endpoints (`node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`). A panic here is not contained to a single transaction — it can crash the node process during core block validation/production, halting block production or causing all nodes that reach the code path to crash simultaneously, which is a severe availability impact for a live chain.

### Likelihood Explanation
Reaching `GasTarget == 0` requires a governance vote/parameter change (in-scope per the prompt's allowed category "governance parameters"), not a single unprivileged transaction. This lowers the practical likelihood versus a fully permissionless trigger, but the complete absence of format validation on this specific field (in clear contrast to the sibling `BaseFeeDenominator` field that is explicitly protected) indicates a real oversight in parameter validation that should be fixed defensively regardless of the governance trust model.

### Recommendation
Add a zero-check for `GasTarget` in `NextMagmaBlockBaseFee` mirroring the existing `BaseFeeDenominator` fallback (e.g., treat `GasTarget == 0` as an error condition or substitute a safe default), and add an explicit `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, consistent with `Kip71BaseFeeDenominator`.

### Proof of Concept
1. Via governance vote (or a test/private chain genesis), set `Governance.KIP71.GasTarget = 0` while leaving Magma enabled.
2. Produce/verify any block where `GasUsed > 0` (almost any block).
3. `NextMagmaBlockBaseFee` computes `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)`; since `parentGasUsed != 0 == gasTarget`, it takes the `parentGasUsed > gasTarget` branch and calls `new(big.Int).Div(x, new(big.Int).SetUint64(0))`, which panics with "division by zero", crashing the node process during header verification/block production. [6](#0-5)

### Citations

**File:** params/kip71_config.go (L58-128)
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

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

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

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
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

**File:** kaiax/gov/param.go (L310-333)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.BaseFeeDenominator, nil
		},
		DefaultValue: uint64(20),
	},
	Kip71GasTarget: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.GasTarget, nil
		},
		DefaultValue: uint64(30000000),
```
