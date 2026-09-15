### Title
Governance-settable `kip71.gastarget = 0` causes division-by-zero panic in `NextMagmaBlockBaseFee`, crashing block production/validation on every node - (File: `params/kip71_config.go`)

### Summary
The KIP-71 base fee update function divides by the governance-controlled `GasTarget` parameter without checking it for zero, while other KIP-71 denominators (e.g. `BaseFeeDenominator`) are explicitly validated to be non-zero. This is directly analogous to the TensorFlow `FractionalMaxPool` CWE-369 bug: an externally-influenced parameter is used as a divisor without a zero-check, causing a crash (denial of service) instead of memory corruption.

### Finding Description
`KIP71Config.NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget` as a divisor: [1](#0-0) [2](#0-1) 

Both the "gas used above target" and "gas used below target" branches divide by `gasTarget` (via `new(big.Int).SetUint64(gasTarget)`), which panics in Go's `math/big` if the divisor is zero. The only branch guarded against `gasTarget == 0` is the exact-equality case `parentGasUsed == gasTarget`, which requires `parentGasUsed == 0` too — extremely unlikely for a live chain.

The `GasTarget` value comes straight from the governance-controlled `Kip71GasTarget` parameter. Compare its parameter definition to the sibling `Kip71BaseFeeDenominator`, which is defensively checked: [3](#0-2) 

`Kip71GasTarget` uses `noopFormatChecker` (no validation at all), while `Kip71BaseFeeDenominator` explicitly requires `v != 0`. This asymmetry indicates the omission is a genuine gap rather than an intentional design choice — the author clearly recognized the need for non-zero denominators for one KIP-71 field but not the other. The governance-approved value is propagated unchanged into the live `KIP71Config` via `ParamSet.ToKip71Config()`: [4](#0-3) 

`NextMagmaBlockBaseFee` is called on every block by block-assembly and block-validation code paths (e.g. `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, `work/worker.go`, `node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`), meaning a zero `GasTarget` would crash header preparation/validation on every node in the network as soon as a block with `parentGasUsed != 0` needs its next base fee computed — i.e., essentially the very next block after the parameter takes effect.

### Impact Explanation
This is a chain-halting denial-of-service: once `kip71.gastarget` is governance-set (or defaults) to `0`, the very next block's base-fee computation panics in every honest node (proposers and validators alike) that executes `NextMagmaBlockBaseFee`, since almost any nonzero gas usage triggers the un-guarded division branch. This satisfies the "state divergence between honest nodes" / crash criteria for governance-parameter-driven analogs — it is a High-severity liveness/availability bug reachable purely through a normal governance parameter update, with no need for a malicious validator, peer, or leaked key.

### Likelihood Explanation
Likelihood depends on governance actually setting `GasTarget` to `0`. Under Kaia's governance model, `Kip71GasTarget` is a settable governance parameter like `Kip71BaseFeeDenominator`; the codebase provides no on-chain or off-chain validation preventing a governance vote (or an operator misconfiguring `genesis.json`) from setting it to `0`, unlike the sibling `BaseFeeDenominator` field, which is explicitly protected. Given the existence of the exact same class of protection for a neighboring field, this looks like an accidental omission rather than a deliberate decision, and it is trivially triggerable by a single governance parameter change (a "governance parameters" analog explicitly in scope).

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` requiring `v != 0` (mirroring `Kip71BaseFeeDenominator`'s checker) in `kaiax/gov/param.go`, and/or add a defensive zero-check inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` that falls back to a safe default (as is already done for `BaseFeeDenominator == 0`) instead of dividing by a potentially-zero `gasTarget`.

### Proof of Concept
1. Governance sets (or genesis configures) `governance.kip71.gastarget = 0` (accepted today because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, performing no validation) — see: [5](#0-4) 
2. Any subsequent block with nonzero gas usage (`parentGasUsed != 0`, hence `parentGasUsed != gasTarget(=0)` and `parentGasUsed > gasTarget`) triggers:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // parentGasUsed - 0
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // division by 0 -> panic
``` [6](#0-5) 
3. Every node calling `NextMagmaBlockBaseFee` while preparing or validating this block's header (block assembly in `work/worker.go`, chain building in `blockchain/chain_makers.go`, tx pool gas price checks in `blockchain/tx_pool.go`) panics, halting block production/validation network-wide.

### Citations

**File:** params/kip71_config.go (L98-103)
```go
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L116-121)
```go
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

**File:** kaiax/gov/paramset.go (L199-207)
```go
func (p *ParamSet) ToKip71Config() *params.KIP71Config {
	return &params.KIP71Config{
		LowerBoundBaseFee:         p.LowerBoundBaseFee,
		UpperBoundBaseFee:         p.UpperBoundBaseFee,
		GasTarget:                 p.GasTarget,
		MaxBlockGasUsedForBaseFee: p.MaxBlockGasUsedForBaseFee,
		BaseFeeDenominator:        p.BaseFeeDenominator,
	}
}
```
