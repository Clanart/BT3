### Title
Division-by-zero panic in KIP-71 base fee calculation when `kip71.gastarget` governance parameter is set to 0 - (File: params/kip71_config.go)

### Summary
The `Kip71GasTarget` governance parameter has no format validation preventing it from being set to `0`, unlike the sibling `Kip71BaseFeeDenominator` parameter which explicitly rejects `0`. When `GasTarget` is `0`, `KIP71Config.NextMagmaBlockBaseFee` performs a `big.Int` division by the zero-valued `gasTarget`, which panics in Go, crashing any node that processes a block or RPC request touching Magma base-fee calculation.

### Finding Description
In `kaiax/gov/param.go`, the parameter table shows an intentional asymmetry: [1](#0-0) 
`Kip71BaseFeeDenominator` uses a `FormatChecker` that requires `v != 0`, while `Kip71GasTarget` uses `noopFormatChecker`, allowing `GasTarget` to be set to `0` via governance vote (header governance or contract governance parameter update).

`NextMagmaBlockBaseFee` in `params/kip71_config.go` explicitly guards against `BaseFeeDenominator == 0` by substituting a fallback value, but performs no equivalent guard for `GasTarget`, which is used directly as a divisor: [2](#0-1) [3](#0-2) [4](#0-3) 

Specifically, `x.Div(x, new(big.Int).SetUint64(gasTarget))` (both in the "gas used above target" and "gas used below target" branches) will panic with "division by zero" whenever `gasTarget == 0` and `parentGasUsed != 0` — which is the vast majority of real blocks (only a block with exactly zero gas used and `gasTarget == 0` would take the early-return equality branch and avoid the panic).

### Impact Explanation
`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are invoked from core consensus and RPC paths, including block validation in `blockchain/block_validator.go`, block assembly in `work/worker.go`, transaction pool base-fee checks in `blockchain/tx_pool.go`, and the public `eth_feeHistory` RPC handler in `node/cn/gasprice/feehistory.go`. A panic here means that once `GasTarget` is set to `0` via governance, every node attempting to validate a new block, build a block, admit transactions to the pool, or serve `eth_feeHistory` for a post-fork block would crash/panic, resulting in a chain-wide denial of service and potential state divergence between nodes that crash versus nodes that haven't yet processed the offending state.

### Likelihood Explanation
Governance parameter changes are a supported and reachable pathway (as opposed to memory-unsafe or validator-compromise-only paths), and this specific parameter (`kip71.gastarget`) is listed among the mutable governance parameters documented in `kaiax/gov/README.md`. The `noopFormatChecker` on `Kip71GasTarget` (contrasted directly with the `!= 0` check present on `Kip71BaseFeeDenominator` in the same file, indicating the omission is not by design but an oversight) makes it straightforward to construct a governance vote/parameter update setting the value to `0`. Once accepted, virtually any subsequent block with nonzero gas usage triggers the panic deterministically.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, mirroring the check already applied to `Kip71BaseFeeDenominator`: [5](#0-4) 
Additionally, as defense-in-depth, add an explicit zero-check/fallback for `gasTarget` inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (analogous to the existing `BaseFeeDenominator == 0` fallback at lines 71-76) so that even a mis-derived or malformed config cannot cause a division-by-zero panic.

### Proof of Concept
1. Submit/approve a governance vote (or contract-governance parameter update) setting `kip71.gastarget` to `0`. Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, this value passes validation in `paramset.go`/`param.go` and is merged into the effective `ParamSet` for subsequent blocks. [6](#0-5) 
2. On the next Magma-enabled block with any nonzero gas usage, any node calling `NextMagmaBlockBaseFee` (via block validation, block assembly, feehistory RPC, etc.) takes the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(0))`. [7](#0-6) 
3. Go's `big.Int.Div` panics on division by zero, crashing the node process — reproducible directly by unit-testing `NextMagmaBlockBaseFee` with `GasTarget: 0` and a nonzero `parentGasUsed`, following the pattern already used in `params/kip71_config_test.go`'s `TestEvenBaseFee`/`TestNextBlockBaseFee` tests. [8](#0-7)

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

**File:** params/kip71_config.go (L96-103)
```go
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

**File:** params/kip71_config_test.go (L91-99)
```go
	testConfig := getTestConfig(common.Big3)
	for _, test := range tests {
		testConfig.Governance.KIP71.LowerBoundBaseFee = test.lowerBoundBaseFee
		testConfig.Governance.KIP71.UpperBoundBaseFee = test.upperBoundBaseFee
		testConfig.Governance.KIP71.GasTarget = test.gasTarget
		testConfig.Governance.KIP71.MaxBlockGasUsedForBaseFee = test.maxBlockGasUsedForBaseFee
		testConfig.Governance.KIP71.BaseFeeDenominator = test.baseFeeDenominator

		even := testConfig.Governance.KIP71.NextMagmaBlockBaseFee(common.Big3, new(big.Int).SetUint64(test.parentBaseFee), test.parentGasUsed)
```
