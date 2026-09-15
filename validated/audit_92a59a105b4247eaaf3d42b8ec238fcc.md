### Title
No maximum upper-bound validation on `governance.unitprice` and KIP-71 base-fee governance parameters allows a governing-node vote to price out ordinary users - ([File: kaiax/gov/param.go])

### Summary
The `GovernanceUnitPrice`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, `Kip71GasTarget`, and `Kip71MaxBlockGasUsedForBaseFee` governance parameters use `noopFormatChecker`, which performs no sanity/range validation beyond a type check. Unlike `FeeRatio`, which is strictly bounded to `[1, 99]` both at the vote/format level and again at consensus execution, these fee-related governance values can be set to any `uint64`, including values so large that ordinary users are effectively priced out of using the chain — the same class of issue reported for `AvailBridge.updateFeePerByte`.

### Finding Description
In `kaiax/gov/param.go`, the parameter definitions show that most fee-impacting KIP-71/UnitPrice parameters have no format/bound check at all: [1](#0-0) [2](#0-1) [3](#0-2) 

`noopFormatChecker` simply returns `true` for any value: [4](#0-3) 

The only cross-validation applied to `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` is a relative consistency check against each other (lower ≤ upper), performed in `headerGovModule.checkConsistency`, with no absolute ceiling: [5](#0-4) 

This stands in contrast to the transaction-level `FeeRatio`, which the codebase explicitly bounds to `[1, MaxFeeRatio)` both when constructing votes/txs and again at consensus (`Transaction.Validate`): [6](#0-5) [7](#0-6) 

Because `Kip71UpperBoundBaseFee`/`GovernanceUnitPrice` directly determine `TxPool`'s enforced gas price and the consensus-verified block `baseFee` via `NextMagmaBlockBaseFee`, an unrestricted value here directly controls the fee every ordinary user must pay to get a transaction included: [8](#0-7) [9](#0-8) 

### Impact Explanation
If a governance vote for `governance.unitprice` or `kip71.upperboundbasefee`/`kip71.lowerboundbasefee` sets an extreme value (e.g. close to `uint64` max), the resulting base fee / unit price becomes the fee every unprivileged transaction sender must pay through `TxPool.validateTx` and block base-fee verification (`VerifyMagmaHeader`). This can make the network practically unusable for ordinary users (denial-of-service via pricing), matching the reported impact for `feePerByte`. It does not directly cause fund loss, but it is a concrete availability/fee-abuse issue reachable by a normal governance vote transaction, with no code-level circuit breaker analogous to the `FeeRatio` bound that exists elsewhere in the very same module.

### Likelihood Explanation
The lack of a bound is a straightforward oversight, not merely an operational safeguard: the same file already implements strict bounding for `FeeRatio` and even simple booleans/enums like `IstanbulPolicy` and `Kip71BaseFeeDenominator` (non-zero), showing that a similar guard was intentionally omitted for the fee-defining KIP-71 parameters and `UnitPrice`. Any vote transaction/governance change (even accidental, e.g. a decimal/unit mistake) that is not later caught by manual review would take effect with no on-chain rejection.

### Recommendation
Add explicit `FormatChecker` upper/lower bound validation (in addition to the existing relative Lower/Upper consistency check) for `GovernanceUnitPrice`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, `Kip71GasTarget`, and `Kip71MaxBlockGasUsedForBaseFee` in `kaiax/gov/param.go`, mirroring the pattern already used for `FeeRatio` (`MaxFeeRatio`) and `IstanbulPolicy`/`Kip71BaseFeeDenominator`, so that governance votes proposing implausibly large fee/base-fee values are rejected at the format-check stage rather than accepted into consensus.

### Proof of Concept
1. A governing node (or council under ballot mode) submits a vote transaction with `Name = "governance.unitprice"` (or `"kip71.upperboundbasefee"`) and `Value = uint64_max` (or any absurdly large number).
2. `NewVoteData` calls `FormatChecker`, which for these parameters is `noopFormatChecker` and always returns `true`, so the vote is accepted: [1](#0-0) 
3. At the next epoch, `headerGovModule.checkConsistency` only checks Lower ≤ Upper (or vice versa) — an extremely large but internally consistent pair passes: [5](#0-4) 
4. `GetParamSet` propagates the value into `ParamSet.UnitPrice`/`UpperBoundBaseFee`, which `TxPool` and `KIP71Config.NextMagmaBlockBaseFee` use directly to gate/require fees from every transaction sender, effectively locking out ordinary users — with no code path rejecting the extreme value.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
```

**File:** kaiax/gov/param.go (L335-345)
```go
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
```

**File:** kaiax/gov/param.go (L357-367)
```go
	Kip71UpperBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
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

**File:** blockchain/types/tx_internal_data.go (L37-39)
```go
// MaxFeeRatio is the maximum value of feeRatio. Since it is represented in percentage,
// the maximum value is 100.
const MaxFeeRatio FeeRatio = 100
```

**File:** blockchain/types/transaction.go (L549-552)
```go
	// Range-check the fee ratio on the consensus path.
	if feeRatio, isRatioTx := tx.FeeRatio(); isRatioTx && !feeRatio.IsValid() {
		return kerrors.ErrFeeRatioOutOfRange
	}
```

**File:** params/kip71_config.go (L58-68)
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
```

**File:** blockchain/tx_pool.go (L868-881)
```go
	} else {
		if pool.rules.IsMagma {
			if pool.gasPrice.Cmp(tx.GasPrice()) > 0 {
				// Ensure transaction's gasPrice is greater than or equal to transaction pool's gasPrice(baseFee).
				logger.Trace("fail to validate gasprice", "pool.gasPrice", pool.gasPrice, "tx.gasPrice", tx.GasPrice())
				return ErrGasPriceBelowBaseFee
			}
		} else {
			// Unitprice policy before magma hardfork
			if pool.gasPrice.Cmp(tx.GasPrice()) != 0 {
				logger.Trace("fail to validate unitprice", "unitPrice", pool.gasPrice, "txUnitPrice", tx.GasPrice())
				return ErrInvalidUnitPrice
			}
		}
```
