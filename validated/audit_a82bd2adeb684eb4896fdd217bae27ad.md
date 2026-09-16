Confirmed. This confirms the exact analog: `Kip71GasTarget` has `FormatChecker: noopFormatChecker` (no minimum-value check), unlike `Kip71BaseFeeDenominator` which explicitly requires `v != 0`, while `GasTarget` (and `LowerBoundBaseFee`/`UpperBoundBaseFee`/`MaxBlockGasUsedForBaseFee`) has no such protection.### Title
`GasTarget` KIP-71 governance parameter lacks a nonzero check, causing a division-by-zero panic that halts block validation on every node - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` without guarding against zero, and the governance parameter definition for `Kip71GasTarget` uses `noopFormatChecker` instead of a nonzero check (unlike the sibling parameter `Kip71BaseFeeDenominator`, which explicitly requires `v != 0`). This is the same bug class as the reported `interestPer3Min` issue: a governance-settable numeric parameter is used unguarded as a divisor/multiplier in a core state-transition formula, and no format/range check prevents an unsafe value (0) from being accepted.

### Finding Description
`NextMagmaBlockBaseFee` computes the base fee for the next block based on `parentGasUsed` vs `gasTarget`: [1](#0-0) 

When `parentGasUsed > gasTarget` (true for any nonzero gas usage when `gasTarget == 0`), the code computes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // gasTarget == 0 -> division by zero
``` [2](#0-1) 

The same division by `gasTarget` also occurs in the "gas used below target" branch: [3](#0-2) 

Go's `math/big.Int.Div` panics on division by zero. Note that the code already anticipated a similar zero-value hazard for `BaseFeeDenominator` and added an explicit fallback (`if kc.BaseFeeDenominator == 0 { ... set to 64 }`), but no equivalent guard exists for `GasTarget`: [4](#0-3) 

The governance parameter registry confirms `Kip71GasTarget` uses `noopFormatChecker`, i.e., any `uint64` value including 0 is accepted, in contrast to `Kip71BaseFeeDenominator` which explicitly enforces `v != 0`: [5](#0-4) 

`NextMagmaBlockBaseFee` (via `VerifyMagmaHeader`) is invoked during block header validation, which every full node/CN must execute for every incoming block: [6](#0-5) 
It is also referenced from `blockchain/block_validator.go`, `blockchain/tx_pool.go`, `work/worker.go`, and `node/cn/gasprice/gasprice.go`, meaning the panic would be reachable in header validation, tx pool gas price updates, block production, and gas price oracle logic across all nodes simultaneously.

### Impact Explanation
If `GasTarget` is ever set to 0 (via a governance vote for the `kip71.gastarget` parameter, which has no minimum-value enforcement), the very next block whose parent has any nonzero `GasUsed` will trigger a division-by-zero panic in `NextMagmaBlockBaseFee`. Because this function is executed deterministically by every node during header validation, tx pool base-fee updates, and worker/gas-price-oracle logic, the panic would crash all nodes in the network simultaneously — a full chain halt (denial of service), which is a direct match to the reported bug class ("incorrectly set numeric governance parameter with no bounds check causes DoS of a core function used by every participant").

### Likelihood Explanation
This requires a governance vote to set `kip71.gastarget` to 0. Governance parameter changes on Kaia are reachable through the standard governance-vote path (explicitly listed as in-scope), and the parameter's format checker (`noopFormatChecker`) does not reject 0, so nothing in the vote acceptance path prevents this misconfiguration from being adopted and activated on the chain, in contrast to the analogous `BaseFeeDenominator` parameter that is explicitly protected against the same class of error.

### Recommendation
Add a nonzero (and reasonably-bounded) `FormatChecker` for `Kip71GasTarget` analogous to the one already used for `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
}
```
Additionally, as defense-in-depth, guard `NextMagmaBlockBaseFee` against `kc.GasTarget == 0` the same way it already guards `BaseFeeDenominator == 0`, so that a config loaded from a stale/pre-existing chain state can never panic.

### Proof of Concept
1. Submit/pass a governance vote setting `kip71.gastarget` to `0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`) [7](#0-6) .
2. Once the vote is applied and takes effect at the target block, produce (or receive) any subsequent block with `GasUsed > 0`.
3. During header validation, `VerifyMagmaHeader` → `NextMagmaBlockBaseFee` is called with `parentHeaderGasUsed > 0` and `kc.GasTarget == 0`, entering the `parentGasUsed > gasTarget` branch and executing `x.Div(x, new(big.Int).SetUint64(0))`, which panics with "division by zero" [8](#0-7) .
4. Because this code path runs on every node during block validation, all nodes panic/crash on the same block, halting the chain.

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

**File:** params/kip71_config.go (L77-102)
```go
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
```

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

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
