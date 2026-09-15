## Finding: Divide-by-zero panic in KIP-71 dynamic base fee calculation via `Kip71GasTarget` governance parameter

### Title
Divide-by-zero panic in `KIP71Config.NextMagmaBlockBaseFee` when `GasTarget` governance parameter is zero - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the governance-controlled `GasTarget` value without any zero-check, unlike its sibling parameter `BaseFeeDenominator`, which the code explicitly guards against being zero. Setting `governance.kip71.gastarget` (param name `Kip71GasTarget`) to `0` causes a Go runtime division-by-zero panic on every node that verifies a post-Magma block header whose parent used any gas — crashing consensus/full-node processing network-wide.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee based on the parent block's gas usage relative to `GasTarget`: [1](#0-0) 

Note the explicit protection for `BaseFeeDenominator == 0` ("To avoid panic, set the fluctuation range small") but **no equivalent guard for `GasTarget`**. When `parentGasUsed != GasTarget`, the code performs: [2](#0-1) 

Both branches (`parentGasUsed > gasTarget` and `parentGasUsed < gasTarget`) execute `x.Div(x, new(big.Int).SetUint64(gasTarget))`. If `gasTarget == 0`, and `parentGasUsed > 0` (virtually always true for any block containing transactions), this is a `big.Int` division by zero, which panics in Go.

`Kip71GasTarget` is registered as a votable governance parameter with **no format validation** preventing zero: [3](#0-2) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly rejects zero via its `FormatChecker`: [4](#0-3) 

Once `GasTarget` is set to `0` (via a normal governance vote — no malicious validator/consensus behavior required, just a valid but under-validated parameter change), the function is invoked from core block-validation and consensus paths reachable by ordinary block processing:
- Header validation for every incoming block: `VerifyMagmaHeader` → `NextMagmaBlockBaseFee`, called from `BlockValidator.validateHeader`: [5](#0-4) 
- Consensus proposal verification: `sb.Verify` → `sb.chain.ValidateHeader`: [6](#0-5) 
- Public RPC `eth_feeHistory` next-base-fee estimation: [7](#0-6) 

### Impact Explanation
This matches the reported bug class (division by zero causing loss of availability, CVSS availability-only impact). Once the parameter is set, **every node** — full nodes syncing headers, validators verifying proposals, and RPC servers computing fee history — panics/crashes when processing the very next post-Magma block whose parent has nonzero gas usage. This is a chain-halting denial-of-service affecting the entire network's availability, not a localized node issue.

### Likelihood Explanation
`Kip71GasTarget` accepts any `uint64` value including `0` (its `FormatChecker` is `noopFormatChecker`), so no additional validation prevents a governance vote from setting it to zero. The trigger condition (`parentGasUsed != 0`) is met by virtually any real-world block. The existing zero-guard for the sibling `BaseFeeDenominator` field shows the maintainers were aware of division-by-zero risk in this exact function but did not apply the same fix to `GasTarget`.

### Recommendation
Add a zero-check for `GasTarget` in `NextMagmaBlockBaseFee` mirroring the existing `BaseFeeDenominator` guard (e.g., treat `0` as an invalid/default value, or short-circuit before the division), and add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, consistent with `Kip71BaseFeeDenominator`.

### Proof of Concept
1. Via governance vote, set `governance.kip71.gastarget = 0` (accepted since `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`).
2. Wait for the vote to take effect after Magma hardfork activation.
3. Any subsequent block with `parentHeaderGasUsed > 0` triggers `NextMagmaBlockBaseFee`'s `x.Div(x, new(big.Int).SetUint64(0))`, panicking every node that calls `VerifyMagmaHeader` (header validation), `sb.Verify` (consensus), or `eth_feeHistory` RPC processing — halting the chain.

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

**File:** params/kip71_config.go (L88-121)
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

**File:** blockchain/block_validator.go (L202-210)
```go
	// Verify Magma basefee rule from governance paramset.
	if v.config.IsMagmaForkEnabled(header.Number) {
		// Skip governance-dependent validation when gov module is not registered.
		if v.mGov != nil {
			govParamSet := v.mGov.GetParamSet(header.Number.Uint64())
			if err := govParamSet.ToKip71Config().VerifyMagmaHeader(header.BaseFee, parent.Number, parent.BaseFee, parent.GasUsed); err != nil {
				return err
			}
		}
```

**File:** consensus/istanbul/backend/backend.go (L420-424)
```go
	// verify the header of proposed block
	err := sb.chain.ValidateHeader(block.Header())
	// ignore errEmptyCommittedSeals error because we don't have the committed seals yet
	if err == nil || err == istanbul.ErrEmptyCommittedSeals {
		return 0, nil
```

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
