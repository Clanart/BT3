Confirmed: `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` — no zero-check unlike `Kip71BaseFeeDenominator` which explicitly rejects `v == 0`. `VerifyMagmaHeader` (called from `block_validator.go`) and `NextMagmaBlockBaseFee` in `params/kip71_config.go` are used by every full node to validate every Magma/post-Magma block header's `BaseFee`.

### Title
Division by zero in KIP-71 base fee calculation via ungated `governance.kip71.gastarget=0` vote - (File: params/kip71_config.go)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` without checking it is nonzero, while the governance parameter registry allows `Kip71GasTarget` to be set to `0` with no format validation. A governance vote setting `governance.kip71.gastarget=0` causes every node to panic (divide by zero) while validating or building the very next block, halting the chain.

### Finding Description
In [1](#0-0)  `gasTarget := kc.GasTarget` is taken directly from governance config and used as a divisor twice, in both the "usage above target" and "usage below target" branches:
```go
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
```
Unlike `BaseFeeDenominator`, which has an explicit zero-guard ("To avoid panic, set the fluctuation range small") at [2](#0-1) , `GasTarget` has no such fallback.

The governance parameter `Kip71GasTarget` is registered with `FormatChecker: noopFormatChecker` in [3](#0-2) , in contrast to `Kip71BaseFeeDenominator` which explicitly requires `v != 0` at [4](#0-3) . This means a governance vote can set `GasTarget` to `0` and it will be accepted without rejection.

Once `GasTarget == 0` takes effect, any block with `parentGasUsed > 0` (i.e., essentially any real block) causes `parentGasUsed > gasTarget` at [5](#0-4) , triggering `big.Int.Div(x, 0)`, which panics in Go's `math/big` package (`big: division by zero`).

`NextMagmaBlockBaseFee` is called from `VerifyMagmaHeader` in the same file, which is invoked by `blockchain/block_validator.go` on every incoming block header, and also from block-building paths (`blockchain/chain_makers.go`, `work/worker.go`, `blockchain/tx_pool.go`, `node/cn/gasprice/gasprice.go`).

### Impact Explanation
Because every full node — not just the proposer — calls `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` for every incoming block, an accepted governance vote setting `GasTarget=0` causes a synchronized, deterministic panic across the entire network at the next block, halting block production and validation network-wide. This is a chain-halting denial of service reachable purely through the governance voting mechanism (an unprivileged/permitted governance parameter change path), matching the in-scope "governance parameters" category.

### Likelihood Explanation
Triggering requires a successful governance vote to change `governance.kip71.gastarget` to `0`. Governance voting on Kaia is restricted to governance council members, but the vulnerability itself is a pure input-validation gap: the parameter registry accepts the value without any bounds check that the equivalent `BaseFeeDenominator` parameter enforces, and the code path lacks the "avoid panic" fallback present for the sibling parameter. Once such a vote is applied (a normal governance operation, not an attack requiring privileged system access beyond normal governance participation), every node deterministically panics on the very next block — making exploitation certain once the parameter change is active.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `v == 0`, consistent with `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check fallback for `gasTarget` inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`, mirroring the existing `BaseFeeDenominator == 0` fallback, so that malformed or legacy configuration data cannot cause a panic.

### Proof of Concept
1. Governance council votes to set `governance.kip71.gastarget = 0`; the vote is accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` (no validation).
2. At the block where this parameter takes effect, any block with nonzero gas usage causes `parentGasUsed (>0) > gasTarget (0)`.
3. `NextMagmaBlockBaseFee` executes `x.Div(x, new(big.Int).SetUint64(0))`, which panics with `division by zero` in Go's `math/big`.
4. This function is called by `VerifyMagmaHeader` during header validation (`blockchain/block_validator.go`) on all nodes, and during block assembly (`work/worker.go`, `blockchain/chain_makers.go`), causing every node in the network to panic/crash simultaneously — a full chain halt.

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

**File:** params/kip71_config.go (L77-121)
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

**File:** kaiax/gov/param.go (L310-323)
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
```

**File:** kaiax/gov/param.go (L324-334)
```go
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
