### Title
Division by zero in KIP-71 base fee calculation when `GasTarget` governance parameter is set to 0 - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` (as `*big.Int`) without checking whether it is zero. The governance parameter `Kip71GasTarget` (`kaiax/gov/param.go`) uses `noopFormatChecker`, i.e. no validation preventing a value of `0` from being accepted and canonicalized. If `GasTarget` is set to `0` through the governance voting mechanism and applied on-chain, every node computing/validating the next block's base fee via `NextMagmaBlockBaseFee` will hit a `big.Int` division by zero and panic, analogous to the libpcap `div #k`/`mod #k` immediate-zero bug (CVE-2026-6244).

### Finding Description
`NextMagmaBlockBaseFee` computes the delta of the base fee when the parent block's gas usage deviates from `GasTarget`: [1](#0-0) 

In the `parentGasUsed > gasTarget` branch, `gasTarget` is converted to a `*big.Int` and used as a divisor without a zero-check: `x.Div(x, new(big.Int).SetUint64(gasTarget))` at line 102, and again `y.Div(y, ...)`-style patterns at line 103/121 for `baseFeeDenominator` (which *is* guarded elsewhere, e.g. `Kip71BaseFeeDenominator`'s `FormatChecker` requires `v != 0` at [2](#0-1) ). No equivalent guard exists for `GasTarget`: [3](#0-2) 

Go's `math/big.Int.Div` panics with `"division by zero"` when the divisor is zero — there is no defensive check like the `if kc.BaseFeeDenominator == 0 { ... }` fallback that exists a few lines above for `BaseFeeDenominator`: [4](#0-3) 

Note that `BaseFeeDenominator == 0` is explicitly handled with a fallback value (64) to "avoid panic", showing the developers were aware of this exact class of bug for one parameter but missed applying the same protection to `GasTarget`.

If `parentGasUsed == 0` and `gasTarget == 0`, the early-return `if parentGasUsed == gasTarget` avoids the panic. But if `gasTarget == 0` and `parentGasUsed > 0` (any block with nonzero gas usage, which is virtually guaranteed on a live chain), the `parentGasUsed > gasTarget` branch is taken and the division-by-zero panic is triggered unconditionally.

`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are called from core block-processing paths including `blockchain/block_validator.go`, `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, `node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`, and `work/worker.go` — meaning both block proposers (mining/building) and validating nodes (header validation, tx pool base fee checks) would panic simultaneously.

### Impact Explanation
If the `governance.kip71.gastarget` parameter is voted to `0` (or otherwise ends up as `0` in the applied `ChainConfig`, e.g. via a governance vote that is accepted because the format checker is a no-op), then as soon as any block is produced with `GasUsed > 0` (essentially every real block), **all full nodes** attempting to compute or verify the next base fee will panic and crash. This is a chain-halting denial-of-service affecting the entire network (both block proposers and verifying nodes), which can also manifest as state divergence between nodes that crash vs. restart with different config timing. This satisfies "state divergence between honest nodes" / chain-halt impact criteria.

### Likelihood Explanation
The governance parameter `Kip71GasTarget` is user-settable through Kaia's on-chain governance vote mechanism, and its `FormatChecker` is `noopFormatChecker`, meaning **no format/range validation is enforced** to reject `0`. This is in stark contrast to the sibling parameter `Kip71BaseFeeDenominator`, whose checker explicitly requires `v != 0`. This asymmetry strongly suggests a missed validation rather than an intentional design, making it plausible that a governance vote (which requires quorum among Governance Council members, not root/node-operator access) could set this value to `0`, either accidentally or maliciously, and immediately halt the chain on the next block. This requires governance council coordination, but no additional protection currently exists to stop it once voted.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` rejecting `v == 0`, mirroring the checker used for `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check/fallback for `kc.GasTarget` inside `NextMagmaBlockBaseFee` (similar to the existing `BaseFeeDenominator == 0` fallback) so a `0` value already present in an existing `ChainConfig`/genesis cannot crash the node.

### Proof of Concept
1. Deploy a Kaia network with Magma/KIP-71 active.
2. Through the governance voting mechanism, submit and pass a vote setting `governance.kip71.gastarget = 0` (accepted because `Kip71GasTarget`'s format checker is a no-op, per [3](#0-2) ).
3. Once the parameter is applied at the governance-effective block, produce any block with `GasUsed > 0` (any ordinary transaction).
4. On computing the next block's base fee, `NextMagmaBlockBaseFee` takes the `parentGasUsed > gasTarget` branch (since `gasTarget == 0 < parentGasUsed`), and executes `x.Div(x, new(big.Int).SetUint64(0))`, causing every node (proposers and validators) to panic with `"division by zero"`, halting the chain.

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

**File:** params/kip71_config.go (L90-109)
```go
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
```

**File:** kaiax/gov/param.go (L310-315)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
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
