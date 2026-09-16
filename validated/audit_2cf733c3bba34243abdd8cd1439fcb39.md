### Title
Governance-settable `Kip71GasTarget` lacks a nonzero check, causing division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
`params.KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` when the parent block's gas usage differs from the target, but unlike the sibling parameter `BaseFeeDenominator`, `GasTarget` has no zero-guard in either the config function or the governance parameter validator. If governance sets `governance.kip71.gastarget` to `0`, any block with `parentGasUsed > 0` will cause a division-by-zero panic in every node computing the next base fee, which happens during consensus header verification on every block.

### Finding Description
`NextMagmaBlockBaseFee` explicitly protects against `BaseFeeDenominator == 0` by substituting a fallback value, with the comment "To avoid panic, set the fluctuation range small": [1](#0-0) 

However, no equivalent protection exists for `GasTarget`, which is used as a divisor twice — once in the "gas used above target" branch and once in the "below target" branch: [2](#0-1) [3](#0-2) 

The only case where `gasTarget == 0` is handled safely is when `parentGasUsed == gasTarget` (both zero), which short-circuits before any division: [4](#0-3) 

But if `parentGasUsed > 0` (i.e., the parent block used any gas at all) and `gasTarget == 0`, execution falls into the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which panics (Go's `big.Int.Div` panics on division by zero, analogous to the Solidity revert/panic in the reported bug).

Crucially, the governance parameter definition for `Kip71GasTarget` uses `noopFormatChecker` — i.e., no format/range validation at all — in contrast to `Kip71BaseFeeDenominator`, which explicitly rejects zero: [5](#0-4) 

This means a governance vote/parameter update (via the `kaiax/gov` module, reachable through the standard governance parameter-setting mechanism used by the governing node/council) can set `GasTarget` to `0` without any rejection.

### Impact Explanation
`NextMagmaBlockBaseFee` (and its header-verification wrapper `VerifyMagmaHeader`) is invoked from consensus-critical block validation in `blockchain/block_validator.go`, block construction in `work/worker.go` and `blockchain/chain_makers.go`, the tx pool's gas price acceptance logic in `blockchain/tx_pool.go`, and the RPC gas price oracle (`node/cn/gasprice`). A panic here is triggered on every full node/validator processing any block with nonzero gas usage after `GasTarget` is set to `0`, resulting in a chain-wide crash/halt of block processing — i.e., all honest nodes fail deterministically at the same point, which is a consensus/liveness-breaking condition rather than a localized error. This matches the "state transition and gas/burn accounting" and "governance parameters" reachable surface named in scope.

### Likelihood Explanation
This requires a governance parameter change (via `kaiax/gov`) setting `GasTarget` to zero. This is not attacker-arbitrary in the sense of a single unprivileged transaction, but it is reachable through the ordinary/expected governance parameter-update flow, which — unlike the sibling `BaseFeeDenominator` field — has zero input validation (`noopFormatChecker`), making an accidental or malicious zero-value proposal directly acceptable by the governance module with no additional privilege beyond normal governance voting. This mirrors exactly the reported bug class: "vesting durations for certain groups are not set correctly or are inadvertently set to zero," occurring "during regular operation ... without proper validation of input parameters."

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, consistent with `Kip71BaseFeeDenominator`:
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
Additionally, as defense in depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should guard against `kc.GasTarget == 0` the same way it already guards `BaseFeeDenominator == 0`, to avoid a panic even if an inconsistent/zero value is loaded from a legacy config or genesis.

### Proof of Concept
1. Through the governance parameter-setting mechanism, propose/vote `governance.kip71.gastarget = 0`. Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, the proposal is accepted with no validation.
2. Once the new parameter set becomes active, any subsequently mined block header must be verified via `KIP71Config.VerifyMagmaHeader` → `NextMagmaBlockBaseFee`, called from `blockchain/block_validator.go` for every incoming block.
3. If the parent block's `GasUsed > 0` (true for essentially any non-empty block), execution reaches:
   ```go
   gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // gasTarget = 0
   x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
   y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // division by zero -> panic
   ``` [6](#0-5) 
4. Every node executing block validation, block building (`work/worker.go`), or the fee oracle panics, halting block processing chain-wide.

### Citations

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

**File:** params/kip71_config.go (L115-122)
```go
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

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
