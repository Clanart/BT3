## Analysis

The reachable analog here is in Kaia's KIP-71 dynamic gas pricing (Magma base-fee) governance parameters. Just like the Meebits `_saleDuration` parameter that lacks a zero/threshold check and is later used as a divisor/denominator in price computation, the `kip71.gastarget` governance parameter lacks any nonzero format check, yet it is used unconditionally as a divisor in the base-fee-update formula.

The parameter definitions show the asymmetry directly: `Kip71BaseFeeDenominator` explicitly rejects zero (`v != 0`), but `Kip71GasTarget` only uses `noopFormatChecker`, which accepts any `uint64` including `0`. [1](#0-0) 

`GasTarget` is then used directly as a divisor in `NextMagmaBlockBaseFee`, in both the "gas used above target" and "gas used below target" branches: [2](#0-1) 

If `GasTarget` is governance-set to `0`, then for any block where `parentGasUsed != 0` (i.e., almost every block), `parentGasUsed > gasTarget` is true, and the code computes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, causing a `big.Int` division-by-zero panic. `BaseFeeDenominator` is defensively defaulted to `64` when zero via an explicit check, but no equivalent safeguard exists for `GasTarget`. [3](#0-2) 

This function is called from consensus-critical, per-block paths such as block validation (`BlockValidator.ValidateHeader` via `NextMagmaBlockBaseFee`/`VerifyMagmaHeader`) and fee-history/gas-price RPC computation, meaning every full node, not just the proposer, would panic when processing the next block after this governance value takes effect. [4](#0-3) [5](#0-4) 

### Title
Missing nonzero check for KIP-71 `gastarget` governance parameter causes division-by-zero panic in base fee computation - (File: params/kip71_config.go)

### Summary
The governance parameter `kip71.gastarget` (`Kip71GasTarget`) has no format/range validation preventing a value of `0`, unlike its sibling parameter `Kip71BaseFeeDenominator`, which explicitly rejects zero. `GasTarget` is used unguarded as a divisor in `KIP71Config.NextMagmaBlockBaseFee`, so setting it to `0` via governance causes a division-by-zero panic on every node computing the next block's base fee.

### Finding Description
`Kip71GasTarget`'s `Param` entry in `kaiax/gov/param.go` uses `noopFormatChecker`, which always returns `true`, so any `uint64` value including `0` passes validation when submitted as a governance vote/parameter update. [6](#0-5) 

`NextMagmaBlockBaseFee` in `params/kip71_config.go` reads `gasTarget := kc.GasTarget` and unconditionally divides by it in both directions of base-fee adjustment (`x.Div(x, new(big.Int).SetUint64(gasTarget))`), with no zero guard analogous to the one present for `BaseFeeDenominator` a few lines above. [7](#0-6) 

This mirrors the reported bug class exactly: a duration/threshold-like parameter that is later used as a divisor/denominator in an economic calculation is missing a minimum/zero check, and once accepted, cannot be corrected without another governance vote (during which every node keeps crashing).

### Impact Explanation
Once `GasTarget = 0` becomes effective (via header governance vote or contract governance, both are legitimate governance update paths, not privileged node-operator config), `NextMagmaBlockBaseFee` panics with a division-by-zero on essentially every subsequent block (since `parentGasUsed` is virtually always `> 0 = gasTarget`). This function is invoked from block header validation and RPC fee estimation paths on every consensus/full node, so the panic is not localized to a single actor — it causes a chain-wide halt/crash across all honest nodes evaluating the new base fee, which is a severe availability/consensus impact (all nodes failing consistently at the same block would look like a full network stall rather than divergence, but the crash itself is a critical liveness failure).

### Likelihood Explanation
Reaching this requires a successful governance parameter update setting `kip71.gastarget` to `0`. This is gated by governance voting mechanics (e.g., under `governance.governancemode = single`, only the governing node's vote counts), so it is not reachable by an arbitrary unprivileged sender in a single transaction, but it is reachable through the standard "governance parameters" update flow explicitly called out as in-scope, and there is no on-chain validation anywhere in the parameter/format-checker pipeline that would reject the value before it takes effect.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), and/or add a defensive fallback inside `NextMagmaBlockBaseFee` (similar to the existing `BaseFeeDenominator == 0` fallback) that substitutes a safe default when `GasTarget == 0` to prevent division by zero regardless of how the value was set.

### Proof of Concept
1. Submit/approve a governance parameter update setting `governance.kip71.gastarget` (i.e., `gov.Kip71GasTarget`) to `0`. This passes validation because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`. [6](#0-5) 
2. Once the parameter set with `GasTarget = 0` becomes active at a subsequent block, any node computing the next block's base fee (in block validation or RPC gas price estimation) calls `KIP71Config.NextMagmaBlockBaseFee` with a header whose `parentGasUsed > 0`.
3. Inside `NextMagmaBlockBaseFee`, execution enters the `parentGasUsed > gasTarget` branch and executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`, causing a runtime panic (division by zero) on every node that processes this block. [8](#0-7)

### Citations

**File:** kaiax/gov/param.go (L310-334)
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
	},
```

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

**File:** params/kip71_config.go (L70-121)
```go
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
```

**File:** node/cn/gasprice/feehistory.go (L99-115)
```go
func (oracle *Oracle) processBlock(bf *blockFees, percentiles []float64) {
	var (
		chainconfig      = oracle.backend.ChainConfig()
		isCurrBlockMagma = chainconfig.IsMagmaForkEnabled(big.NewInt(int64(bf.blockNumber)))
		isNextBlockMagma = chainconfig.IsMagmaForkEnabled(big.NewInt(int64(bf.blockNumber + 1)))

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
