### Title
`kip71.gastarget` Governance Parameter Lacks Nonzero Check, Causing Division-by-Zero Panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The KIP-71 governance parameter `kip71.gastarget` is registered with a no-op format checker that accepts any `uint64` value, including `0`. This value is later used directly as a divisor inside `NextMagmaBlockBaseFee`, which computes the base fee for every block and is invoked from consensus-critical header verification (`VerifyMagmaHeader`). Setting `GasTarget` to `0` via governance causes a Go runtime panic (division by zero) rather than the intended graceful error handling that already exists for the sibling parameter `BaseFeeDenominator`.

### Finding Description
`Kip71GasTarget` is defined with `FormatChecker: noopFormatChecker`, meaning no bounds or nonzero check is enforced when the parameter is set via governance: [1](#0-0) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly enforces `v != 0`: [2](#0-1) 

The unchecked `GasTarget` value flows into `KIP71Config.GasTarget` and is used as a raw `big.Int` divisor in `NextMagmaBlockBaseFee`: [3](#0-2) 

Note that the code already defends against `BaseFeeDenominator == 0` by substituting a fallback value ("To avoid panic, set the fluctuation range small"), but no equivalent guard exists for `gasTarget`. If `parentGasUsed != gasTarget` (which is the common case once `gasTarget == 0` and any gas is used), the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which is a `big.Int` division by zero and panics in Go.

This function is invoked from block header validation/verification: [4](#0-3) 

`GasTarget` is explicitly documented as a governance-mutable KIP-71 parameter (not one of the "immutable" ones), meaning it is expected to be changeable by governance in normal operation: [5](#0-4) 

This is directly analogous to the reported `Auction.sol` issue: an owner/governance-controlled setter (`setAuctionDecrement` ↔ governance vote setting `kip71.gastarget`) lacks a minimum-value check, and the unguarded value is later used as a divisor in a critical downstream computation (`settleAuction` ↔ `NextMagmaBlockBaseFee`/`VerifyMagmaHeader`), causing a division-by-zero fault.

### Impact Explanation
Because `NextMagmaBlockBaseFee` is called during block header verification (post-Magma hardfork) for every block, a panic here is far more severe than the original report's "one function reverts" scenario — it can crash the Go process on every node that verifies/produces a block after the parameter change takes effect, i.e. a full-network denial-of-service / chain halt rather than a single blocked auction settlement. This satisfies the "acceptance/processing divergence, invalid block, or DoS impacting all nodes" bar for Medium/High severity.

### Likelihood Explanation
Exploitation requires a party with governance authority (the governing node under `governance.governancemode = single`, analogous to the "owner" role in the original report) to vote/set `kip71.gastarget = 0` through the normal header-governance vote or contract-governance parameter update mechanism — a standard, permitted governance action, not a "malicious validator/node" compromise. Since `kip71.gastarget` is documented as a normal mutable parameter (unlike `reward.proposerupdateinterval`/`reward.stakingupdateinterval`, which are documented as immutable on Mainnet), there is no additional social/consensus barrier preventing this value from being set, and the code contains zero defensive check, mirroring exactly the missing bounds-check pattern from the original report.

### Recommendation
Add an explicit nonzero (and reasonable upper-bound) check in the `Kip71GasTarget` parameter definition's `FormatChecker`, matching the pattern already used for `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
```
Additionally, as defense-in-depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should guard against `kc.GasTarget == 0` the same way it already guards `BaseFeeDenominator == 0`, to prevent a panic even if an invalid value slips through from a legacy/misconfigured chain config.

### Proof of Concept
1. Governing node submits a governance vote (or contract-governance call) setting `kip71.gastarget = 0`. This passes `Kip71GasTarget`'s `noopFormatChecker` (`kaiax/gov/param.go`) unconditionally.
2. Once the vote takes effect, `ParamSet.GasTarget` becomes `0` and is propagated into `KIP71Config.GasTarget` via `ToKip71Config()`.
3. On the next block after any nonzero gas usage, `parentGasUsed (>0) != gasTarget (0)`, taking the `parentGasUsed > gasTarget` branch in `NextMagmaBlockBaseFee`, which executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` — a `big.Int` division by zero — causing a runtime panic in every node executing `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` for that block, halting the chain.

### Citations

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

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
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

**File:** kaiax/gov/README.md (L17-34)
```markdown
```
<mutable parameters>
governance.deriveshaimpl
governance.governingnode
governance.govparamcontract
governance.unitprice
istanbul.committeesize
kip71.basefeedenominator
kip71.gastarget
kip71.lowerboundbasefee
kip71.maxblockgasusedforbasefee
kip71.upperboundbasefee
reward.kip82ratio
reward.mintingamount
reward.ratio
reward.stakingrewardthreshold
reward.useflexreward

```
