### Title
Division-by-zero in `NextMagmaBlockBaseFee` when governance sets `GasTarget = 0` - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the governance-controlled `GasTarget` parameter with no zero-check, while the analogous `BaseFeeDenominator` parameter *is* explicitly guarded against zero. `GasTarget` can be set to `0` through the governance parameter system because its `FormatChecker` is a no-op, unlike `Kip71BaseFeeDenominator`, which explicitly rejects zero.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget` as a divisor in two branches: [1](#0-0) [2](#0-1) 

Note that `baseFeeDenominator` has an explicit zero-guard just above it: [3](#0-2) 

but `gasTarget` (`kc.GasTarget`) has no equivalent fallback. If `parentGasUsed != gasTarget` (true for essentially any block with `gasTarget == 0` and nonzero gas usage), the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. Go's `big.Int.Div`/`Quo` panics with `"division by zero"` when the divisor is zero, exactly analogous to the FPE crash in the referenced lwext4 CVE caused by an unvalidated zero `lb_size` used as a divisor.

The root cause is a missing validation gap in the governance parameter registry: while `Kip71BaseFeeDenominator`'s `FormatChecker` explicitly enforces `v != 0`, `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, allowing `0` to pass validation and be written into `ChainConfig.Governance.KIP71.GasTarget`: [4](#0-3) 

Once `GasTarget = 0` is accepted as a governance parameter (via the standard governance vote/parameter-update mechanism) and applied for a future block, every node that computes the base fee for that block — during header validation (`VerifyMagmaHeader`), block insertion, `SuggestPrice`/`FeeHistory` (`node/cn/gasprice/feehistory.go`), or the eth_feeHistory RPC — will panic. Because this function is called deterministically on all block-processing nodes (including via `blockchain/block_validator.go` header verification and `node/cn/gasprice` RPC handling), the impact is a network-wide denial of service / node crash upon processing the transition block, rather than a localized bug in a single peer.

### Impact Explanation
A crash in base-fee computation is triggered on every full node (validators and RPC nodes alike) as soon as they process a block at or after the height where `GasTarget = 0` takes effect, or whenever `FeeHistory`/`SuggestPrice` RPC endpoints are queried for a block range spanning that height. This is a network-wide denial of service that halts block processing and public RPC service — matching the CVSS 3.1 profile of the source CVE (`AV:L/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H`), here manifesting as availability loss for the whole chain rather than a single filesystem mount.

### Likelihood Explanation
Exploitation requires a governance parameter update setting `Kip71GasTarget` to `0` to be accepted and take effect — this happens through the normal governance voting/parameter-update path, and there is no format-level rejection of zero, unlike the sibling `BaseFeeDenominator` parameter which is explicitly protected. Because the value only needs to be *accepted* as a valid format (not otherwise bounds-checked anywhere else in the codebase, per the searches performed), a single successful governance parameter update is sufficient to arm the crash for the entire network at the next relevant block.

### Recommendation
Add an explicit zero-check to `Kip71GasTarget`'s `FormatChecker` in `kaiax/gov/param.go` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), and/or add a defensive fallback in `NextMagmaBlockBaseFee` in `params/kip71_config.go` analogous to the existing `baseFeeDenominator == 0` fallback, so that `gasTarget == 0` cannot reach the `big.Int.Div` call.

### Proof of Concept
1. Submit/approve a governance parameter update setting `Kip71GasTarget` to `0` (accepted because `FormatChecker` is `noopFormatChecker`, per `kaiax/gov/param.go:324-334`).
2. Once this parameter set becomes active for a block whose parent has `parentGasUsed != 0` (almost any real block), `NextMagmaBlockBaseFee` (`params/kip71_config.go:58`) executes `parentGasUsed > gasTarget` (0) branch, computing `x.Div(x, new(big.Int).SetUint64(0))` at `params/kip71_config.go:102`, or the symmetric branch at line 120 — both panic with "division by zero".
3. This function is invoked during header verification (`VerifyMagmaHeader`) and via `node/cn/gasprice/feehistory.go`'s `processBlock` (`kip71Config.NextMagmaBlockBaseFee(...)` at line 112), so any node processing the block or serving `eth_feeHistory`/`eth_gasPrice` crashes.

**Uncertainty note:** I was unable to fully trace the exact governance vote-submission entry point (e.g., whether it requires special "governing node" privilege vs. any transaction sender) within the available tool budget, so the privilege level required to actually push a `GasTarget = 0` update through governance could not be fully confirmed from the indexed code — this should be verified against `kaiax/gov/headergov` vote-processing logic before treating this as fully unprivileged-reachable.

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

**File:** params/kip71_config.go (L98-103)
```go
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L115-121)
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
