### Title
Unchecked GasTarget=0 causes division-by-zero panic in KIP-71 base fee calculation - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` in `params/kip71_config.go` divides by the governance-controlled `GasTarget` parameter without validating it is non-zero, unlike its sibling parameter `BaseFeeDenominator`, which has an explicit zero-guard. If `GasTarget` is ever `0`, any block with nonzero gas usage triggers a `big.Int` division-by-zero panic during base fee computation.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using the governance parameter `GasTarget`: [1](#0-0) 

Note that `BaseFeeDenominator` is defensively checked and falls back to a safe default of `64` if zero, to explicitly "avoid panic": [1](#0-0) 

However, `GasTarget` receives no equivalent protection. When `parentGasUsed != gasTarget`, the code performs: [2](#0-1) 
and in the decreasing-gas branch: [3](#0-2) 

Both branches call `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which panics if `gasTarget == 0`. The early-return short-circuit `if parentGasUsed == gasTarget { return ... }` only avoids the panic when `parentGasUsed` is also exactly `0` — any block that uses gas at all (a near-certainty in production) with `GasTarget == 0` will hit the panic.

Critically, the governance parameter validator for `Kip71GasTarget` uses a `noopFormatChecker`, meaning no validation prevents governance from setting `GasTarget` to zero, in contrast to `Kip71BaseFeeDenominator`, whose `FormatChecker` explicitly rejects zero (`v != 0`): [4](#0-3) 

This function is invoked in the consensus-critical header verification path (`VerifyMagmaHeader`), the block/fee-history RPC oracle path, and worker block assembly, making the panic reachable by every node processing a block once such a governance value takes effect.

### Impact Explanation
A panic in `NextMagmaBlockBaseFee` during header verification would crash node processes that verify or build blocks, since this function is called from consensus/verification code paths (`blockchain/block_validator.go`, `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, `work/worker.go`) as well as RPC-facing fee-estimation code (`node/cn/gasprice/feehistory.go`, `node/cn/gasprice/gasprice.go`). A network-wide crash on every full node upon processing the first block after `GasTarget=0` takes effect would halt the chain — a denial-of-service impacting availability for all users, transactions, and RPC callers, satisfying the "state divergence/consensus halt" impact category.

### Likelihood Explanation
`GasTarget=0` can only be reached through a governance parameter change, since `Kip71GasTarget`'s `FormatChecker` is a no-op that permits any `uint64` value including zero. This requires governance/governing-node authority to submit such a vote — this is not directly triggerable by an ordinary unprivileged transaction sender. This significantly limits likelihood: it is a latent defect rather than a directly externally-triggerable bug, and would only manifest from a misconfigured or malicious governance vote rather than from public RPC/transaction input alone.

### Recommendation
Add a zero-guard for `GasTarget` analogous to the existing safeguard for `BaseFeeDenominator` in `NextMagmaBlockBaseFee`, falling back to a safe default (e.g., the default `GasTarget` value) when `kc.GasTarget == 0`. Additionally, update the `Kip71GasTarget` `FormatChecker` in `kaiax/gov/param.go` to reject `0`, mirroring `Kip71BaseFeeDenominator`'s existing check, so that invalid governance votes cannot be admitted in the first place.

### Proof of Concept
1. Submit/approve a governance vote setting `Kip71GasTarget` to `0` (permitted because `FormatChecker` is `noopFormatChecker`). [5](#0-4) 
2. Once the parameter activates, any subsequent block with `GasUsed > 0` causes `NextMagmaBlockBaseFee` to execute the `parentGasUsed > gasTarget` (or `<` ) branch and divide by `gasTarget = 0`: [6](#0-5) 
3. All nodes calling this during header verification (`VerifyMagmaHeader`) or fee-history RPC processing panic, halting block production/validation network-wide.

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

**File:** params/kip71_config.go (L97-103)
```go
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
