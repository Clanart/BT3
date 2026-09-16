### Title
Division-by-zero panic in `NextMagmaBlockBaseFee` when `kip71.gastarget` governance parameter is set to 0 - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee`, the function that computes each block's `baseFee`, divides by the governance-controlled `GasTarget` parameter without checking it is non-zero. Unlike `BaseFeeDenominator`, which has an explicit `v != 0` format check when registered as a governance parameter, `GasTarget` uses `noopFormatChecker` and can be set to `0` through governance voting, leading to a division-by-zero panic during block base-fee calculation/verification.

### Finding Description
`NextMagmaBlockBaseFee` computes `baseFeeDelta` by dividing by `gasTarget` whenever `parentGasUsed != gasTarget`: [1](#0-0) 

If `kc.GasTarget == 0` and any gas was used in the parent block (`parentGasUsed > 0`), the branch `parentGasUsed > gasTarget` is taken and `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` divides by zero, which panics in Go's `big.Int.Div`.

The `GasTarget` parameter is governance-controlled and registered with `noopFormatChecker`, i.e., no value-range validation, unlike `Kip71BaseFeeDenominator` which explicitly rejects zero: [2](#0-1) 

This contrasts with the `BaseFeeDenominator` zero-guard already present in `NextMagmaBlockBaseFee` (lines 70-76 of the same file), confirming the developers were aware of the div-by-zero risk for one parameter but did not apply the same protection to `GasTarget`.

### Impact Explanation
`NextMagmaBlockBaseFee` is consensus-critical: it is used to both compute and verify the block header's `baseFee` (via `VerifyMagmaHeader`) and is invoked from block-building (`work/worker.go`) and header-verification-adjacent code paths (`node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`, `blockchain/tx_pool.go`, `blockchain/chain_makers.go`). A panic here during block production or header verification would crash the node process (or in the worst case, cause divergent behavior between nodes if the panic is not uniformly triggered), effectively producing a chain-halting bug once `GasTarget` is voted to `0` and a block with nonzero gas usage is produced. This satisfies the "state divergence between honest nodes" / "acceptance of an invalid transaction or block" class of impact from a governance parameter update.

### Likelihood Explanation
Triggering requires a governance vote setting `kip71.gastarget = 0`, which is gated by governance council voting rather than being reachable by an arbitrary unprivileged transaction sender. However, since the "governance parameters" surface is explicitly listed as in-scope, and the format checker for this parameter performs no validation at all (`noopFormatChecker`), any council member proposing/voting `0` (accidentally or maliciously) would immediately and deterministically crash every node upon the next non-empty block — a very low bar compared to typical governance misconfigurations, and directly analogous to the reported "internal function used in several public/internal paths without protection against division by zero."

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`) that rejects `0`, mirroring the existing check for `Kip71BaseFeeDenominator`: [3](#0-2) 
Additionally, add a defensive zero-check inside `NextMagmaBlockBaseFee` before dividing by `gasTarget`, consistent with the existing `baseFeeDenominator == 0` fallback pattern: [4](#0-3) 

### Proof of Concept
1. Governance council votes `kip71.gastarget = 0` (accepted since `noopFormatChecker` performs no validation) — [5](#0-4) .
2. Once the new parameter set becomes active, any block whose parent used any gas (`parentGasUsed > 0`) causes `parentGasUsed > gasTarget(0)` to be true.
3. `NextMagmaBlockBaseFee` executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, calling `big.Int.Div(x, 0)`, which panics — [6](#0-5) .
4. This panics on every node computing/verifying the block's base fee, halting the chain.

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

**File:** params/kip71_config.go (L99-121)
```go
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
