Found a concrete analog: a missing minimum-value validation on the governance parameter `kip71.gastarget` that leads to a division-by-zero panic in the KIP-71 (Magma) base-fee calculation, causing a chain-wide crash/halt — the same bug class described in the report (missing min/max validation → DoS).This confirms the vulnerability: `Kip71BaseFeeDenominator` explicitly rejects `0` (`v != 0`), but the neighboring `Kip71GasTarget` parameter uses `noopFormatChecker`, which accepts any `uint64` value including `0`. This asymmetry is the root cause.

### Title
Missing minimum-value validation on `kip71.gastarget` governance parameter causes division-by-zero panic in KIP-71 base fee calculation - (File: `kaiax/gov/param.go`, `params/kip71_config.go`)

### Summary
The governance parameter `kip71.gastarget` is registered with `FormatChecker: noopFormatChecker` [1](#0-0) , meaning any `uint64` value, including `0`, is accepted as valid both via header-vote governance and contract governance (KIP-81). This value is later used as an unguarded divisor in `NextMagmaBlockBaseFee`, which computes the base fee for every block under the Magma/KIP-71 fee mechanism.

### Finding Description
`NextMagmaBlockBaseFee` explicitly guards against `BaseFeeDenominator == 0` (falling back to `64`) [2](#0-1) , showing the developers were aware that governance-settable divisors need to be defended against zero. However, no equivalent guard exists for `GasTarget`. When `parentGasUsed > gasTarget`, the code computes:
```
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
``` [3](#0-2) 
If `gasTarget == 0`, and any transaction was included in the parent block (`parentGasUsed > 0`), this triggers `big.Int.Div` division by zero, which panics.

This is reachable because:
1. `Kip71GasTarget` has `FormatChecker: noopFormatChecker` [1](#0-0) , unlike `Kip71BaseFeeDenominator` which correctly rejects `0` [4](#0-3) .
2. In header governance vote consistency checks (`checkConsistency`), `Kip71GasTarget` is in the list of parameters requiring "no more checks here" beyond `NewVoteData()` format validation [5](#0-4) . Only `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` get cross-parameter consistency checks (lines 188-201 of the same file).
3. Contract governance (`GovParam` contract, KIP-81 on-chain voting) reuses the exact same `Params` map and the same `Set()`/format-checking logic [6](#0-5) , so a `setParamIn("kip71.gastarget", 0)` transaction submitted through the `GovParam` contract is accepted identically.
4. `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` is invoked during block assembly (`work/worker.go`) and block validation (`blockchain/block_validator.go`) on every node in the network for every block.

### Impact Explanation
Once a `GasTarget = 0` vote (via header governance vote or `GovParam.setParamIn`) is accepted and becomes effective, every subsequent block whose parent used any gas will trigger a `big.Int` division-by-zero panic inside `NextMagmaBlockBaseFee`, called from both the block producer (`work/worker.go`) and every validating node (`blockchain/block_validator.go`). This crashes the process on every full node/CN in the network simultaneously — a consensus-halting, network-wide denial of service, not merely an isolated crash. This is far more severe than the original report's "unexpected outcomes" — it is a total-network liveness failure requiring manual intervention/hard fix to recover.

### Likelihood Explanation
Governance parameter changes require validator/council-level voting authority (via header-embedded votes or the on-chain `GovParam` contract), which is a privileged but explicitly in-scope category per the assessment rules ("governance parameters"). No additional runtime precondition beyond a single malicious or erroneous vote is needed — the format checker itself is the only gate, and it is a no-op for this specific parameter. Given the parallel, correct guard already implemented for `BaseFeeDenominator`, it strongly indicates this is an unintentional omission rather than a deliberate design decision, making accidental as well as intentional triggering plausible.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and audit `Kip71MaxBlockGasUsedForBaseFee` similarly) that rejects `0`, e.g.:
```go
FormatChecker: func(cv any) bool {
    v, ok := cv.(uint64)
    return ok && v != 0
},
```
mirroring the existing guard on `Kip71BaseFeeDenominator` [7](#0-6) . As defense in depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should also guard against `gasTarget == 0` directly (similar to the existing `BaseFeeDenominator == 0` fallback) so that a value stored in `ChainConfig` (e.g., via genesis or config load) cannot cause a panic either.

### Proof of Concept
1. A validator/council member submits a header vote (or a `GovParam.setParamIn` contract-governance transaction) setting `kip71.gastarget = 0`.
2. `NewVoteData`/`Set` accepts it because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` [1](#0-0) ; `checkConsistency` performs no additional cross-checks for this parameter [5](#0-4) .
3. Once the parameter set becomes effective at the target block, any block whose parent has `parentHeaderGasUsed > 0` triggers, in `NextMagmaBlockBaseFee`:
```go
gasTarget := kc.GasTarget // == 0
...
} else if parentGasUsed > gasTarget { // 0 > 0 is false only if parentGasUsed==0 too; any real gas usage triggers this
    ...
    y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // panic: division by zero
``` [8](#0-7) 
4. This function is called both when the block producer computes the header's `BaseFee` (`work/worker.go`) and when every node validates it (`blockchain/block_validator.go`), so the panic occurs network-wide, halting block production/validation across all nodes simultaneously.

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

**File:** kaiax/gov/param.go (L324-333)
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
```

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

**File:** params/kip71_config.go (L77-103)
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
```

**File:** kaiax/gov/headergov/impl/header.go (L213-220)
```go
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```

**File:** kaiax/gov/contractgov/impl/getter.go (L17-32)
```go
func (c *contractGovModule) GetParamSet(blockNum uint64) gov.ParamSet {
	m, err := c.contractGetAllParamsAt(blockNum)
	if err != nil {
		return *gov.GetDefaultGovernanceParamSet()
	}

	ret := *gov.GetDefaultGovernanceParamSet()
	for k, v := range m {
		err = ret.Set(k, v)
		if err != nil {
			return *gov.GetDefaultGovernanceParamSet()
		}
	}

	return ret
}
```
