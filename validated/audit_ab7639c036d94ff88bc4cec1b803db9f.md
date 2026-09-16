## Analog Found

### Title
Unchecked division-by-zero in KIP-71 (Magma) base fee calculation via `kip71.gastarget` governance vote - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee`, the function that computes the next block's base fee under the Magma fork, divides by the governance-controlled `GasTarget` parameter without ever validating that it is non-zero. Unlike the sibling parameter `BaseFeeDenominator` (which is explicitly checked for zero, `require v != 0`, and falls back to a safe default of 64), `GasTarget` has no such protection and can be set to `0` through a normal, protocol-defined governance vote. This directly parallels CVE-2023-51104: a numeric field that reaches a division operation without a zero-check, causing a crash (divide-by-zero) instead of an error path.

### Finding Description
In [1](#0-0) , `BaseFeeDenominator` is defensively checked for zero and replaced with a fallback value. `GasTarget`, however, is used unchecked: [2](#0-1) 

When `gasTarget == 0` and `parentGasUsed > 0`, the branch `parentGasUsed > gasTarget` is taken and the code executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))`, i.e. `big.Int.Div(x, 0)`, which panics ("division by zero") in Go's `math/big` package — functionally the same bug class as MuPDF's floating-point divide-by-zero crash.

The root cause is that the governance parameter definition for `Kip71GasTarget` uses `noopFormatChecker`, allowing any `uint64` value including `0`, in contrast to `Kip71BaseFeeDenominator` which explicitly rejects zero: [3](#0-2) 

The header-governance vote/verification pipeline does not add any additional consistency check for `gov.Kip71GasTarget` beyond the generic format check — it is passed through as an always-accepted vote: [4](#0-3) 

`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` execute during header verification and block preparation on every node (`blockchain/block_validator.go`, `work/worker.go`, `blockchain/chain_makers.go`) and are also exposed to public callers through `eth_feeHistory`/`kaia_feeHistory` via `node/cn/gasprice/feehistory.go`: [5](#0-4) 

Once `GasTarget=0` is ratified through the governance mechanism (an epoch-block `header.Governance` update derived from council votes), every subsequent block whose parent used any nonzero gas will trigger the panic during both block production and block verification, or during any `eth_feeHistory`/`kaia_feeHistory` RPC call for a block after the parameter takes effect.

### Impact Explanation
Once `GasTarget` is set to `0` via governance, all full nodes computing the next base fee (during header verification, block sealing, and public `feeHistory` RPC serving) will panic simultaneously — a chain-wide crash/Denial-of-Service affecting the entire network, not merely a local process. This is a High severity consensus-availability bug: it can halt block production network-wide, matching the "state divergence between honest nodes / acceptance-of-invalid-transaction-or-block" style impact criteria (here manifesting as universal crash rather than divergence), and is directly reachable from a legitimate governance vote path with no additional privileged/off-chain access needed beyond the standard, protocol-sanctioned voting mechanism.

### Likelihood Explanation
The likelihood depends on the ability to cast/ratify a `kip71.gastarget` vote. In `single` governance mode this requires the designated governing node; in committee/DAO governance modes it requires a majority of council votes — this is a normal, in-protocol action rather than an out-of-scope "operator-only" or "malicious-validator" primitive, since governance voting is explicitly part of the reachable attack surface for this analysis. Given the total absence of a format check for zero on this specific parameter (in contrast to the adjacent `BaseFeeDenominator` parameter, which was clearly hardened against the same issue), this looks like an oversight rather than an intentional design decision, making exploitation straightforward for any party capable of getting a governance vote ratified.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check) in `kaiax/gov/param.go`, and/or add a defensive zero-check with a safe fallback inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` before any `Div` operation involving `gasTarget`, consistent with the existing handling of `BaseFeeDenominator`.

### Proof of Concept
1. Through the governance mechanism (single governing node vote, or council majority vote depending on `GovernanceMode`), submit and ratify a vote: `governance_vote("kip71.gastarget", 0)`.
2. Once ratified at an epoch boundary, the new `GasTarget = 0` takes effect in the `ParamSet` for subsequent blocks.
3. On the next block whose parent has `GasUsed > 0` (virtually guaranteed on any active network), every node calls `KIP71Config.NextMagmaBlockBaseFee` during `VerifyMagmaHeader`/block preparation, hitting `x.Div(x, new(big.Int).SetUint64(0))`, which panics.
4. Additionally, any client calling `eth_feeHistory`/`kaia_feeHistory` for blocks in this range triggers the same panic via `node/cn/gasprice/feehistory.go`'s `processBlock`.

Note: I could not locate an explicit runtime recover/guard around this specific `Div` call in the block-verification or RPC call paths within the indexed portions of the repository; if such a recover exists elsewhere (e.g., a generic panic-recovery middleware around RPC handlers or consensus message processing), it would reduce the network-wide crash impact to a per-call RPC failure. This should be verified directly in a full checkout since the index may not include all wrapper/middleware code.

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

**File:** params/kip71_config.go (L88-109)
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
```

**File:** kaiax/gov/param.go (L310-333)
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

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
