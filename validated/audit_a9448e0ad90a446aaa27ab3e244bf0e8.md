### Title
Ungated `kip71.gastarget` Governance Vote Enables Division-by-Zero Panic in Magma Base-Fee Calculation, Halting All Nodes - (File: params/kip71_config.go)

### Summary
The Kaia governance module validates individual KIP-71 (Magma dynamic base fee) parameters in isolation but never checks the interrelationship between `kip71.gastarget`, `kip71.basefeedenominator`, and `kip71.maxblockgasusedforbasefee`, nor does it reject a zero `GasTarget`. This mirrors the JUSD finding: individual reserve/global parameters (`InitialMortgageRate`, `liquidationMortgageRate`, `borrowFeeRate`) are checked in isolation without validating their combined safety, allowing an unsafe update to slip through and later trigger catastrophic failure at execution time.

### Finding Description
`Kip71GasTarget` uses `noopFormatChecker`, meaning any `uint64` value including `0` passes format validation: [1](#0-0) 

The header-vote consistency checker (`checkConsistency`) also does not special-case `Kip71GasTarget`; it falls into the generic "no additional check" bucket alongside other KIP-71 parameters: [2](#0-1) 

Only `LowerBoundBaseFee`/`UpperBoundBaseFee` are cross-checked against each other; `GasTarget` and `MaxBlockGasUsedForBaseFee` have no such relational or non-zero validation.

In `NextMagmaBlockBaseFee`, `gasTarget` is used unconditionally as a divisor once the parent block's gas usage differs from the target: [3](#0-2) 

If `gasTarget == 0` and the parent block used any gas at all (`parentGasUsed > 0`), execution enters the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. `big.Int.Div` panics on division by zero. Note that the code explicitly guards against `BaseFeeDenominator == 0` with a fallback default (line 71-76 of the same file), showing the developers were aware of the general divide-by-zero risk class, yet the same protection was omitted for `GasTarget`.

`NextMagmaBlockBaseFee` is invoked from `VerifyMagmaHeader`, which is called from the header/block validation path (`blockchain/block_validator.go`), the block-building path (`work/worker.go`, `blockchain/chain_makers.go`), and the gas-price oracle (`node/cn/gasprice/gasprice.go`). None of these call sites wrap the call in a `recover()`.

### Impact Explanation
Once `kip71.gastarget` is set (or defaults) to `0` via a ratified governance vote, every node computing or verifying the very next Magma-era block with nonzero gas usage will panic inside `NextMagmaBlockBaseFee`. Because this function sits on the core block insertion/verification and block-building paths, an unrecovered panic crashes the node process. Since all conforming nodes run the same logic, this affects the entire network simultaneously, producing a full chain halt — a critical availability failure analogous to how the JUSD report's unchecked parameter interactions caused system-wide unsafe liquidations once triggered.

### Likelihood Explanation
Governance parameter changes are explicitly in-scope (per the review criteria) and are a normal, expected operational action (GC on-chain/header voting per KIP-81). The bug requires no malicious validator collusion beyond a single (possibly accidental) vote for `kip71.gastarget = 0` (or ratification of a governance parameter update to that value) being ratified — there is no code path rejecting it. Given the parameter's `noopFormatChecker` and absence of any relational check, this is a straightforward, deterministic trigger once the value is set and a block consumes any gas.

### Recommendation
Add an explicit `FormatChecker` for `Kip71GasTarget` (and `Kip71MaxBlockGasUsedForBaseFee`) rejecting `0`, matching the existing pattern used for `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
}
```
Additionally, add a defensive zero-check with a safe fallback (mirroring the existing `BaseFeeDenominator == 0` fallback) directly inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`, so that even a value that slips past governance validation cannot crash block processing.

### Proof of Concept
1. Submit/ratify a governance vote (`governance_vote` with `name: "kip71.gastarget", value: 0`) as a council member — this passes `checkConsistency` and `NewVoteData`/`FormatChecker` validation since `Kip71GasTarget` uses `noopFormatChecker`: [1](#0-0) 
2. Once the vote is ratified into `header.Governance` and becomes effective, the next Magma block header (`parentHeaderGasUsed > 0`) processed by any node calls `KIP71Config.NextMagmaBlockBaseFee` during header verification/block building: [4](#0-3) 
3. `parentGasUsed > gasTarget(=0)` is true, and `x.Div(x, new(big.Int).SetUint64(0))` panics with "division by zero," crashing the node process across the network at that block.

### Citations

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

**File:** kaiax/gov/headergov/impl/header.go (L188-220)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
	case gov.AddValidator, gov.RemoveValidator:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}
		if slices.Contains(vote.Value().([]common.Address), params.GoverningNode) {
			return ErrGovNodeInValSetVoteValue
		}
		return nil
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```

**File:** params/kip71_config.go (L58-103)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
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
```
