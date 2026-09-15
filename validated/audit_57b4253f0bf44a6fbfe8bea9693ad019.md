### Title
Missing zero-value validation on `Kip71GasTarget` governance parameter causes division-by-zero panic in `NextMagmaBlockBaseFee` - ([File: params/kip71_config.go])

### Summary
`NextMagmaBlockBaseFee` divides by the governance-controlled `GasTarget` value without checking whether it is zero, unlike the sibling `BaseFeeDenominator` parameter which has an explicit zero-guard. `GasTarget` can be set to `0` through a normal governance vote because its `FormatChecker` is a no-op and its consistency check performs no additional validation, so the chain will accept and apply a `GasTarget = 0` parameter, after which every subsequent block header/base-fee computation panics.

### Finding Description
`KIP71Config.NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget := kc.GasTarget` as a divisor: [1](#0-0) 

While `baseFeeDenominator` is explicitly guarded against zero (`if kc.BaseFeeDenominator == 0 { ... fallback to 64 }`), no equivalent guard exists for `gasTarget`: [2](#0-1) [3](#0-2) 

When `parentGasUsed > gasTarget` (true for essentially any nonzero gas usage once `gasTarget == 0`), the code executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))`, which is `big.Int.Div(x, 0)` — a guaranteed runtime panic ("division by zero"), directly analogous to the ImageMagick YUV sampling-factor bug where a logic error let an invalid (zero) divisor bypass validation.

The root cause is that `Kip71GasTarget` is registered with a `noopFormatChecker`, unlike other divisor-like parameters: [4](#0-3) 

And the header-vote consistency checker (`checkConsistency`) explicitly treats `gov.Kip71GasTarget` as needing "no more checks here" beyond the (no-op) format check: [5](#0-4) 

Compare this with `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which do receive dedicated cross-field consistency checks in the same function: [6](#0-5) 

Thus a governance vote setting `governance.kip71.gastarget=0` passes format validation, passes header-vote consistency validation, gets included in the epoch's `Governance` field, and becomes the active `GasTarget` in the `ParamSet` from the next epoch onward.

### Impact Explanation
Once `GasTarget` becomes `0` and takes effect, `NextMagmaBlockBaseFee` is invoked from multiple consensus-critical and publicly reachable paths:
- Block header validation, via `KIP71Config.VerifyMagmaHeader` for every new block on every full/validator node.
- Block assembly (`work/worker.go`), causing the block-producing node to panic when trying to compute the next base fee.
- The gas price oracle used to serve public JSON-RPC calls (`node/cn/gasprice/gasprice.go`, `isRelaxedNetwork`) and fee-history RPC (`node/cn/gasprice/feehistory.go`, `processBlock`), both reachable by any public RPC caller requesting `eth_gasPrice`/`eth_feeHistory`/`eth_maxPriorityFeePerGas`. [7](#0-6) [8](#0-7) 

Because the divide-by-zero panics on essentially every block with `parentGasUsed > 0`, this results in a chain-wide denial of service: every honest node crashes when validating/building blocks or serving basic gas-price RPCs, matching the CWE-369 / availability-impact class of the ImageMagick advisory (CVSS `A:L`).

### Likelihood Explanation
Setting a governance parameter requires either being (or gaining majority influence over) the governing council/node in single mode, or a validator vote in council mode — this is more privileged than an arbitrary unprivileged RPC caller, but it is squarely within the in-scope "governance parameters" category. Given there is no format or consistency check rejecting `GasTarget = 0` anywhere in the vote pipeline (`param.go`'s `noopFormatChecker` and `header.go`'s pass-through case), a single malicious or erroneous vote is sufficient to trigger the crash network-wide once the vote is finalized at the epoch boundary — no additional conditions are required beyond `parentGasUsed != 0`, which is normal chain activity.

### Recommendation
Add an explicit zero-guard for `GasTarget` in `NextMagmaBlockBaseFee` analogous to the existing `BaseFeeDenominator` fallback (e.g., treat `GasTarget == 0` as an error or substitute a safe default), and update `Kip71GasTarget`'s `FormatChecker` in `kaiax/gov/param.go` to reject `0` (and any other value that could later be used as a divisor), rather than relying on `noopFormatChecker`. Additionally, add a `checkConsistency` case for `gov.Kip71GasTarget` in `kaiax/gov/headergov/impl/header.go` to reject zero (or otherwise unsafe) values before they can be accepted into `Governance` header data.

### Proof of Concept
1. Via a governance vote (single-mode governing node, or council majority), submit `governance.kip71.gastarget = 0`. This passes `noopFormatChecker` and passes `checkConsistency` (no validation for this key).
2. At the next epoch boundary, the vote is finalized into the header `Governance` field and the `ParamSet` for subsequent blocks now has `GasTarget = 0`.
3. On the next block where `parentGasUsed > 0` (essentially any block with transactions), `NextMagmaBlockBaseFee` executes:
   - `gasUsedDelta := parentGasUsed - 0`
   - `x := parentBaseFee * gasUsedDelta`
   - `y := x.Div(x, big.NewInt(0))` → **panic: division by zero**
4. This panic occurs in every node calling `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` (block validation, block assembly, and public gas-price RPC handlers), crashing full nodes and validators and halting the chain.

### Citations

**File:** params/kip71_config.go (L70-77)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
```

**File:** params/kip71_config.go (L99-103)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-122)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

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

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
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

**File:** node/cn/gasprice/gasprice.go (L332-336)
```go
func (oracle *Oracle) isRelaxedNetwork(header *types.Header) bool {
	pset := oracle.govModule.GetParamSet(header.Number.Uint64() + 1)
	nextBaseFee := pset.ToKip71Config().NextMagmaBlockBaseFee(header.Number, header.BaseFee, header.GasUsed)
	return nextBaseFee.Cmp(big.NewInt(int64(pset.LowerBoundBaseFee))) <= 0
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
