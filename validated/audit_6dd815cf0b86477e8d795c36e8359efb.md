The divide-by-zero bug class from CVE-2022-38865 has a direct, more severe analog in the Kaia base-fee (KIP-71/Magma) calculation.

### Title
Divide-by-zero panic in KIP-71 base fee calculation via `governance.kip71.gastarget=0` - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` without any zero-guard, unlike the sibling parameter `BaseFeeDenominator`, which explicitly falls back to a safe default when zero. `Kip71GasTarget`'s format checker is a no-op, allowing a ratified governance vote to set `GasTarget = 0`, which subsequently triggers a Go runtime panic (`integer divide by zero`) on every node computing the base fee for the next block.

### Finding Description
`NextMagmaBlockBaseFee` computes `gasTarget := kc.GasTarget` and later divides by it in both the "gas used above target" and "gas used below target" branches: [1](#0-0) [2](#0-1) 

Compare this to `BaseFeeDenominator`, which is defensively defaulted to 64 if zero to "avoid panic": [3](#0-2) 

No equivalent guard exists for `GasTarget`. The governance parameter definition for `Kip71GasTarget` uses `noopFormatChecker`, in contrast to `Kip71BaseFeeDenominator`, whose `FormatChecker` explicitly rejects `v == 0`: [4](#0-3) 

Because `Kip71GasTarget`'s format checker performs no bounds validation, a vote of `("kip71.gastarget", uint64(0))` passes `NewVoteData` and consistency checks — the `checkConsistency` switch statement treats `Kip71GasTarget` as a param requiring "no more checks" beyond `NewVoteData`'s format checks: [5](#0-4) 

Once such a vote is cast by the governing node and ratified at the epoch boundary, `GetParamSet` returns `GasTarget = 0` for all subsequent blocks. On the very next block where `parentGasUsed != 0` (i.e., any block that isn't completely empty), `NextMagmaBlockBaseFee` enters either the "above target" or "below target" branch and divides by `gasTarget = 0`, causing a Go integer-division panic. Because `NextMagmaBlockBaseFee` (via `VerifyMagmaHeader`) is invoked during header verification, block assembly, and RPC gas-price estimation on every node, this results in a consensus-wide chain halt.

### Impact Explanation
This is a state transition / block assembly denial-of-service: once the malicious value is ratified, `NextMagmaBlockBaseFee` is called by:
- `blockchain/block_validator.go` (header verification for all imported blocks)
- `work/worker.go` (block assembly by proposers)
- `node/cn/gasprice/gasprice.go` and `node/cn/gasprice/feehistory.go` (RPC gas price oracle for `eth_feeHistory`/`eth_gasPrice`)

A panic here on header verification/assembly crashes every full/consensus node, i.e., the entire network halts (equivalent to acceptance-of-invalid-parameter causing state divergence/crash across all honest nodes), which is a Medium/High severity denial-of-service far beyond the original single-process crash in MPlayer.

### Likelihood Explanation
The precondition is a single ratified governance vote setting `kip71.gastarget = 0`. This requires governing-node vote authority (single-mode governance, as documented), which is a normal governance action reachable through the `governance_vote` RPC and header ratification process — no attacker needs elevated node/consensus-breaking capability beyond the existing (in-scope) governance parameter update path. Given `noopFormatChecker` performs zero validation, this is trivially reachable by anyone with governing-node voting rights, and the very next non-empty block after ratification triggers the panic deterministically.

### Recommendation
Add a zero-guard for `GasTarget` in `NextMagmaBlockBaseFee`, mirroring the existing `BaseFeeDenominator` fallback, and/or update `Kip71GasTarget`'s `FormatChecker` in `kaiax/gov/param.go` to reject `v == 0`, consistent with `Kip71BaseFeeDenominator`.

### Proof of Concept
1. Governing node casts vote `("kip71.gastarget", uint64(0))` via `governance_vote` RPC; `NewVoteData`/`checkConsistency` accept it since `Kip71GasTarget` uses `noopFormatChecker` and no additional consistency check exists for it. [5](#0-4) 
2. Vote is ratified at the epoch boundary; `GetParamSet` returns `GasTarget = 0` from the next epoch onward.
3. On the first subsequent block where `parentGasUsed != 0` (e.g., any block containing at least one transaction), `NextMagmaBlockBaseFee` computes `gasUsedDelta = parentGasUsed - 0` and divides `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, causing an unrecovered integer-divide-by-zero panic in every node evaluating the header/base fee. [1](#0-0)

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

**File:** params/kip71_config.go (L99-103)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-121)
```go
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
