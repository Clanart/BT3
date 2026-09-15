Based on my investigation, I found a concrete analog to the reported bug class in the governance parameter validation code.

### Title
Missing zero/lower-bound validation on `GovernanceUnitPrice` and KIP-71 base fee governance parameters allows disabling the network's minimum gas price floor - ([File: kaiax/gov/param.go])

### Summary
The `Params` map in `kaiax/gov/param.go` defines, for every governance-tunable parameter, a `Canonicalizer` and a `FormatChecker` used to validate values before they are accepted from header votes or the on-chain `GovParam` contract. Several price/fee-related parameters use `noopFormatChecker` — a checker that unconditionally returns `true` — instead of a checker rejecting invalid (e.g., zero) values. This mirrors exactly the reported bug class: a price-setting function/parameter that accepts zero without validation.

### Finding Description
`GovernanceUnitPrice`, `Kip71GasTarget`, `Kip71LowerBoundBaseFee`, and `Kip71UpperBoundBaseFee` are all registered with `FormatChecker: noopFormatChecker`: [1](#0-0) [2](#0-1) 

Compare this to sibling parameters in the same map that *do* enforce sane bounds, e.g. `IstanbulCommitteeSize` requires `v > 0` and `Kip71BaseFeeDenominator` requires `v != 0`: [3](#0-2) [4](#0-3) 

These values flow directly into consensus-critical, unauthorized-value-movement-relevant logic:
- `GovernanceUnitPrice` becomes `ChainConfig.UnitPrice`, which pre-Magma is the mandatory gas price enforced by the tx pool (`ErrInvalidUnitPrice` check): [5](#0-4) 
- `Kip71LowerBoundBaseFee`/`UpperBoundBaseFee`/`GasTarget`/`BaseFeeDenominator` feed `KIP71Config.NextMagmaBlockBaseFee`, which computes the mandatory per-block base fee (post-Magma consensus rule enforced via `VerifyMagmaHeader`): [6](#0-5) 

If `LowerBoundBaseFee` (and/or `UpperBoundBaseFee`) is set to `0` via governance vote or the `GovParam` contract's `setParamIn`/`setParam` (only gated by contract ownership, not by any value sanity check): [7](#0-6) , the network's computed base fee can converge to `0`, and if `UnitPrice` is separately voted to `0` pre-Magma, the tx pool's mandatory gas-price check becomes a no-op (`0 == 0`).

### Impact Explanation
A zero base fee / unit price removes the network's minimum transaction cost floor. Since Kaia enforces exact/greater-or-equal price matching against this floor in `blockchain/tx_pool.go` (`ErrInvalidUnitPrice`, `ErrGasPriceBelowBaseFee`), a `0` value effectively disables the network's spam/DoS protection for free — attackers (any transaction sender) could flood the pool and blocks with zero-cost transactions, and the base-fee burn mechanism described by KIP-71/KIP-82 (which underlies reward distribution ratios) would compute a zero burn amount, silently changing the effective fee/reward economics enforced by consensus. This is a state-divergence and economic-integrity risk consistent with "Medium" severity, gated by the fact that it requires a successful governance vote/contract call to set the value — the code path itself has no safety net once that authorization is obtained.

### Likelihood Explanation
Low-to-medium likelihood: it requires either (a) governing-node header votes to pass a `0` value for `GovernanceUnitPrice`, or (b) the `GovParam` contract owner (governance council via KIP-81 on-chain voting) calling `setParam`/`setParamIn` with `0`. No additional application-level guard blocks this once that authorization threshold is met, unlike sibling parameters (`IstanbulCommitteeSize`, `Kip71BaseFeeDenominator`) which do enforce non-zero values — showing the omission is inconsistent within the same file rather than an intentional design choice.

### Recommendation
Add `FormatChecker` bounds to `GovernanceUnitPrice`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, and `Kip71GasTarget` in `kaiax/gov/param.go` analogous to the `v > 0` / `v != 0` checks already used for `IstanbulCommitteeSize` and `Kip71BaseFeeDenominator`, to prevent governance from driving the effective gas price/base fee to `0` unless that is an explicitly intended, reviewed policy (as opposed to an unchecked omission).

### Proof of Concept
1. Governance council (or a governing node under `GovernanceMode: "single"`) submits a header vote or calls `GovParam.setParamIn("governance.unitprice", true, <0 encoded>, ...)`.
2. `contractGetAllParamsAtFromAddr`/`ParseContractCall` in `kaiax/gov/contractgov/impl/getter.go` accepts the value because `Params[GovernanceUnitPrice].FormatChecker` (`noopFormatChecker`) never rejects `0`.
3. The effective `ParamSet.UnitPrice` becomes `0`; before Magma, `blockchain/tx_pool.go`'s `pool.gasPrice.Cmp(tx.GasPrice())` check now accepts `tx.GasPrice() == 0`, permitting free transaction spam network-wide.
4. Equivalently, setting `Kip71LowerBoundBaseFee`/`UpperBoundBaseFee` to `0` causes `KIP71Config.NextMagmaBlockBaseFee` in `params/kip71_config.go` to legitimately compute and have validators accept a `0` base fee post-Magma, which is enforced as valid per `VerifyMagmaHeader`.

### Citations

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
```

**File:** kaiax/gov/param.go (L265-281)
```go
	IstanbulCommitteeSize: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			if !ok {
				return false
			}
			return v > 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Istanbul == nil {
				return nil, errors.New("istanbul is not set")
			}
			return c.Istanbul.SubGroupSize, nil
		},
		DefaultValue: uint64(21),
	},
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

**File:** kaiax/gov/param.go (L324-367)
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
	Kip71MaxBlockGasUsedForBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.MaxBlockGasUsedForBaseFee, nil
		},
		DefaultValue: uint64(60000000),
	},
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

**File:** params/kip71_config.go (L45-68)
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

**File:** contracts/bindings/gov/GovParam.go (L520-539)
```go
// SetParam is a paid mutator transaction binding the contract method 0x3f8aa624.
//
// Solidity: function setParam(string name, bool exists, bytes val, uint256 activation) returns()
func (_GovParam *GovParamTransactor) SetParam(opts *bind.TransactOpts, name string, exists bool, val []byte, activation *big.Int) (*types.Transaction, error) {
	return _GovParam.contract.Transact(opts, "setParam", name, exists, val, activation)
}

// SetParam is a paid mutator transaction binding the contract method 0x3f8aa624.
//
// Solidity: function setParam(string name, bool exists, bytes val, uint256 activation) returns()
func (_GovParam *GovParamSession) SetParam(name string, exists bool, val []byte, activation *big.Int) (*types.Transaction, error) {
	return _GovParam.Contract.SetParam(&_GovParam.TransactOpts, name, exists, val, activation)
}

// SetParam is a paid mutator transaction binding the contract method 0x3f8aa624.
//
// Solidity: function setParam(string name, bool exists, bytes val, uint256 activation) returns()
func (_GovParam *GovParamTransactorSession) SetParam(name string, exists bool, val []byte, activation *big.Int) (*types.Transaction, error) {
	return _GovParam.Contract.SetParam(&_GovParam.TransactOpts, name, exists, val, activation)
}
```
