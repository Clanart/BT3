### Title
Governance can set `Kip71GasTarget` to zero causing a division-by-zero panic in base fee calculation - (File: params/kip71_config.go)

### Summary
The Kaia governance parameter framework validates many mutable governance parameters via `FormatChecker` functions, but `Kip71GasTarget` uses `noopFormatChecker`, allowing it to be set to `0`. This value is later used as a divisor in the KIP-71 dynamic base fee formula, causing a division-by-zero panic during block base fee computation — a consensus-critical, network-wide crash triggered purely by a governance-parameter update.

### Finding Description
Governance parameters such as `Kip71BaseFeeDenominator` are explicitly guarded against zero values (`FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }`), but the analogous `Kip71GasTarget` parameter uses `noopFormatChecker`, which accepts any `uint64` value including `0`. [1](#0-0) 

This parameter feeds directly into `KIP71Config.NextMagmaBlockBaseFee`, which computes the next block's base fee. When the parent block's gas usage exceeds `gasTarget` (which is trivially true if `gasTarget == 0` and any gas was used), the function divides by `gasTarget`: [2](#0-1) 

Since `gasTarget` is a `uint64` wrapped in a `big.Int` via `new(big.Int).SetUint64(gasTarget)`, calling `x.Div(x, new(big.Int).SetUint64(0))` triggers a runtime panic in Go's `math/big` package (division by zero). The same divisor is reused in the "gas used below target" branch as well: [3](#0-2) 

This mirrors exactly the class of bug described in the external report: a governance-configurable timing/threshold parameter that is not validated against being set to zero, even though other closely related parameters (`BaseFeeDenominator`) in the same struct *are* validated for this exact condition — showing the omission is an inconsistency rather than an intentional design choice.

`NextMagmaBlockBaseFee` is called on every block header construction and verification path once the Magma hard fork is active, e.g. in block generation and fee history processing: [4](#0-3) [5](#0-4) 

### Impact Explanation
If `Kip71GasTarget` is set to `0` via governance (contract governance `setParam`/`setParamIn` on the `GovParam` contract, or header-vote governance), every node computing the base fee for the next Magma-enabled block will panic as soon as a block with nonzero gas usage is produced. Because base fee calculation is part of core block header construction/verification, this affects all honest nodes identically — resulting in a full network halt/crash rather than a divergence, which is a severe availability impact on Kaia's consensus and public RPC (since RPC nodes computing `eth_feeHistory`/`estimateGas` next-base-fee values also call this function).

### Likelihood Explanation
Reachability requires a successful governance parameter change to `Kip71GasTarget`, which is normally performed by governance council members/governing node under Kaia's governance rules (not an arbitrary unprivileged transaction sender). However, unlike most other numeric KIP-71 parameters, there is no on-chain safeguard preventing this specific misconfiguration, whereas the sibling parameter `Kip71BaseFeeDenominator` explicitly guards against the same class of input. A single governance mistake or malicious/compromised governing-node vote is sufficient to trigger the panic on every node, with no additional attacker capability required.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and audit `Kip71MaxBlockGasUsedForBaseFee`/`Kip71UpperBoundBaseFee`/`Kip71LowerBoundBaseFee` similarly) requiring `v != 0`, consistent with the existing `Kip71BaseFeeDenominator` check, and additionally add a defensive zero-check/fallback inside `NextMagmaBlockBaseFee` (as already done for `BaseFeeDenominator`) so a bad historical or externally-fed value cannot crash the node.

### Proof of Concept
1. Governance participants vote (or call `GovParam.setParam`/`setParamIn`) to set `kip71.gastarget` to `0`, which passes validation because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`. [6](#0-5) 
2. Once this parameter set becomes active and a block is mined with `GasUsed > 0` under the Magma fork, `NextMagmaBlockBaseFee` executes the branch `parentGasUsed > gasTarget` and computes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`. [7](#0-6) 
3. `big.Int.Div` panics on division by zero, crashing every node attempting to build or verify the next block header, effectively halting the chain.

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

**File:** blockchain/chain_makers.go (L305-307)
```go
	if chain.Config().IsMagmaForkEnabled(header.Number) {
		header.BaseFee = chain.Config().Governance.KIP71.NextMagmaBlockBaseFee(parent.Number(), parent.Header().BaseFee, parent.GasUsed())
	}
```

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
