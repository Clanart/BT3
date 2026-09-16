## Analysis

CVE-2016-9922 is a divide-by-zero denial-of-service caused by an unvalidated divisor (blit pitch) that reaches an integer-division operation in QEMU's `cirrus_do_copy`. The reachable analog in this codebase is a governance-set `GasTarget` parameter that is used as an unchecked divisor in Kaia's KIP-71 (Magma) base-fee calculation, unlike the sibling parameter `BaseFeeDenominator`, which explicitly rejects zero.

### Title
Unvalidated `Kip71GasTarget` governance parameter enables divide-by-zero panic in KIP-71 base fee computation, halting block processing on all nodes - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` (twice, in both the "gas used above target" and "gas used below target" branches) without checking it is non-zero, while the sibling `BaseFeeDenominator` value has an explicit zero-guard in the same function. The governance parameter registry that admits `Kip71GasTarget` updates uses `noopFormatChecker`, allowing `GasTarget` to be set to `0`, unlike `Kip71BaseFeeDenominator` which enforces `v != 0`.

### Finding Description
`NextMagmaBlockBaseFee` explicitly guards against `BaseFeeDenominator == 0` (falls back to `64`), but performs no equivalent guard for `GasTarget`: [1](#0-0) 

When the parent block's gas usage differs from `gasTarget`, the function divides by `gasTarget` directly: [2](#0-1) [3](#0-2) 

If `gasTarget == 0`, any block with non-zero gas usage (`parentGasUsed > gasTarget`) enters the first branch, computing `y := x.Div(x, new(big.Int).SetUint64(gasTarget))`, which panics on division by zero (`big.Int.Div` panics for divisor `0`).

The root cause is that the `Kip71GasTarget` parameter definition in the governance parameter table lacks a zero-rejecting `FormatChecker`, unlike its sibling `Kip71BaseFeeDenominator`: [4](#0-3) 

This value flows unchecked from governance into `ParamSet.ToKip71Config()` and then into `NextMagmaBlockBaseFee`/`VerifyMagmaHeader`, both of which are called during block header validation, block building, and gas price estimation (`blockchain/block_validator.go`, `work/worker.go`, `node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`), i.e. on every honest node processing every block after the parameter takes effect.

### Impact Explanation
Once a `GasTarget = 0` governance vote is finalized and takes effect, every node that validates or builds the next Magma-enabled block with nonzero gas usage will panic inside `NextMagmaBlockBaseFee`, crashing the node process. Because this code runs in the consensus-critical header-verification/block-building path (`blockchain/block_validator.go`), this is not merely a single-node crash but a chain-wide liveness failure — all honest full nodes/validators processing the affected block would crash simultaneously, halting the network. This matches the CVE's bug class (crafted-but-technically-in-range value driving an unguarded integer division to zero, causing a process crash/DoS) but at blockchain-consensus scale rather than a single hypervisor guest.

### Likelihood Explanation
Setting `GasTarget` requires a successful governance vote (the parameter is part of `KIP71Config` set through the governance module), so this is not exploitable by a fully unprivileged actor in a single transaction. However, per the scope rules "governance parameters" are explicitly listed as an in-scope reachable path, and the finding demonstrates a genuine input-validation gap: the codebase already recognizes the need to defend `BaseFeeDenominator` against zero but omits the identical, and more severe, defense for `GasTarget`. Any governance process that permits parameter changes without additional off-chain sanity checks (e.g., a single governing node under `GovernanceMode: "single"`, as shown in the sample chain config) could trigger this with one parameter-change vote.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), and additionally add a defensive zero-guard directly inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (analogous to the existing `BaseFeeDenominator` fallback) so that a `GasTarget` of `0` cannot reach the `big.Int.Div` calls regardless of how the value was set.

### Proof of Concept
1. Governance sets `governance.kip71.gastarget` (Kip71GasTarget) to `0` via a governance vote; the format checker (`noopFormatChecker`) admits the value. [5](#0-4) 
2. Once the vote takes effect at the target block, `ParamSet.ToKip71Config()` propagates `GasTarget: 0` into the `KIP71Config` used by header validation. [6](#0-5) 
3. Any subsequent block with `parentGasUsed > 0` causes `NextMagmaBlockBaseFee` to execute `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`. [2](#0-1) 
4. `big.Int.Div` panics with "division by zero", crashing every node that validates/builds that block (`blockchain/block_validator.go` calls into this path during header verification), halting the chain.

### Citations

**File:** params/kip71_config.go (L70-78)
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
