### Title
`Kip71GasTarget` governance parameter accepts zero with no format validation, causing a division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The `NextMagmaBlockBaseFee` function computes the KIP-71 dynamic base fee using several governance-controlled inputs (`GasTarget`, `BaseFeeDenominator`, `MaxBlockGasUsedForBaseFee`, bounds) without validating that all of them are non-zero/sane, mirroring the reported flatcoin issue where `_currentFundingRate` inputs are used without validity checks. While `BaseFeeDenominator` has an explicit zero-guard, `GasTarget` does not, and its governance `FormatChecker` is a no-op that accepts any `uint64` including `0`.

### Finding Description
`NextMagmaBlockBaseFee` divides by `gasTarget` in both the "gas used above target" and "gas used below target" branches: [1](#0-0) [2](#0-1) [3](#0-2) 

Note that the code explicitly guards against `BaseFeeDenominator == 0` by falling back to `64`, but performs no equivalent guard for `gasTarget == 0`.

The governance parameter `Kip71GasTarget` is registered with `FormatChecker: noopFormatChecker`, meaning any `uint64` value, including `0`, passes validation when set via governance vote (header vote or GovParam contract): [4](#0-3) 

Consistency checking for this vote in header governance also does not add extra validation — it is grouped with parameters that are accepted unconditionally: [5](#0-4) 

If `GasTarget` is set to `0` and `parentGasUsed != 0` (the overwhelmingly common case), execution enters the `parentGasUsed > gasTarget` branch:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // parentGasUsed - 0
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // division by big.Int(0) -> panic
```
`math/big`'s `Int.Div` panics on division by zero. This function is invoked in many consensus/critical paths across every node type, including block production (`work/worker.go` `commitNewWork`), block header verification (`VerifyMagmaHeader`), tx pool base-fee updates on every new head (`blockchain/tx_pool.go`), and gas price oracle/fee history RPC handlers, so a single governance vote setting `GasTarget=0` deterministically crashes all Kaia nodes (CN/PN/EN) as soon as the parameter becomes effective and the next block is processed. [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
A successful `Kip71GasTarget=0` governance change causes a deterministic panic in `NextMagmaBlockBaseFee`, which is called from block production, header verification, tx pool reset, and RPC gas price/fee-history code paths on every node. This results in a network-wide chain halt / crash of consensus nodes and RPC nodes — a severe availability impact analogous to (but more severe than) the reported "incorrect calculation" issue, since here the missing input validation leads to an unrecoverable panic rather than merely an incorrect value.

### Likelihood Explanation
Reachability requires only a successful governance parameter change of `kip71.gastarget` to `0`, which is explicitly allowed by the code (`noopFormatChecker`, unconditional acceptance in `checkConsistency`). No additional privilege beyond normal governance voting/administration is needed, and governance parameters are within the analog scope defined for this scan.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (similar to `Kip71BaseFeeDenominator`'s `v != 0` check), and add a defensive zero-check in `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator == 0` fallback) so that `gasTarget == 0` cannot reach the `big.Int` division.

### Proof of Concept
1. Submit/approve a governance vote (header vote or GovParam contract update) setting `kip71.gastarget` to `0`. `NewVoteData`/`Add` canonicalizes it as `uint64(0)` and `noopFormatChecker` accepts it: [4](#0-3) .
2. `checkConsistency` for `gov.Kip71GasTarget` performs no extra validation and returns `nil`, so the vote is accepted: [5](#0-4) .
3. Once the parameter takes effect at block N, any node computing the next block's base fee (miner via `commitNewWork`, tx pool via `reset`, or any node validating block N+1's header via `VerifyMagmaHeader`) calls `NextMagmaBlockBaseFee(parentNumber, parentBaseFee, parentGasUsed)` with `parentGasUsed != 0`.
4. Inside `NextMagmaBlockBaseFee`, `gasTarget = 0`, `parentGasUsed > gasTarget` is true, and `y.Div(x, new(big.Int).SetUint64(0))` panics with "division by zero", crashing the node process.

### Citations

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

**File:** params/kip71_config.go (L96-103)
```go
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
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

**File:** blockchain/tx_pool.go (L573-580)
```go
	// It needs to update gas price of tx pool since magma hardfork
	if pool.rules.IsMagma {
		pset := pool.govModule.GetParamSet(newHead.Number.Uint64() + 1)
		pool.gasPrice = pset.ToKip71Config().NextMagmaBlockBaseFee(newHead.Number, newHead.BaseFee, newHead.GasUsed)
		if pool.rules.IsOsaka {
			pool.blobBaseFee = params.CalcBlobFee(pool.gasPrice)
		}
	}
```

**File:** work/worker.go (L378-384)
```go
	if self.config.IsMagmaForkEnabled(nextBlockNum) {
		// NOTE-Kaia NextBlockBaseFee needs the header of parent, self.chain.CurrentBlock
		// So above code, TxPool().Pending(), is separated with this and can be refactored later.
		pset := self.govModule.GetParamSet(nextBlockNum.Uint64())
		nextBaseFee = pset.ToKip71Config().NextMagmaBlockBaseFee(parent.Number(), parent.Header().BaseFee, parent.GasUsed())
		pending = types.FilterTransactionWithBaseFee(pending, nextBaseFee)
	}
```
