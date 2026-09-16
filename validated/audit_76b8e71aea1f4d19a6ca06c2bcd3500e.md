This confirms the mechanism: `pset.ToKip71Config().NextMagmaBlockBaseFee(...)` is used by both block proposers (`work/worker.go`) and validators (`blockchain/block_validator.go`) to compute/verify the base fee, and `pset` comes from `GovModule.GetParamSet()` which merges headergov and, post-Kore, **contractgov** (`kaiax/gov/impl/getter.go`). While headergov enforces `LowerBoundBaseFee <= UpperBoundBaseFee` via `checkConsistency` in `kaiax/gov/headergov/impl/header.go` (lines 188-201), the contract-governance path (`kaiax/gov/contractgov/impl/getter.go`, `GetParamSet`) only applies the per-field `FormatChecker`, which for both `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` is `noopFormatChecker` (`kaiax/gov/param.go` lines 335-367) — there is no cross-field consistency check anywhere in the contractgov ingestion path.

### Title
Contract-governance (KIP-81 GovParam) can set `kip71.lowerboundbasefee` > `kip71.upperboundbasefee`, bypassing the consistency check enforced for header-vote governance, corrupting KIP-71 base-fee pricing - ([File: kaiax/gov/contractgov/impl/getter.go])

### Summary
Kaia enforces KIP-71 dynamic base-fee bounds (`lowerboundbasefee` / `upperboundbasefee`) that must satisfy `lower <= upper` so that `NextMagmaBlockBaseFee` produces a sane fee. This invariant is checked only for the header-vote (on-chain single-governor) governance path, not for the contract-based (KIP-81, on-chain multi-party) governance path.

### Finding Description
The header-vote governance flow validates KIP-71 bound consistency in `checkConsistency`: [1](#0-0) 

However, when the parameter set comes from contract governance (`GovParam` contract via KIP-81, active post-Kore), `contractGovModule.GetParamSet` only runs `ret.Set(k, v)`, which internally applies just the per-parameter `FormatChecker` — no cross-field consistency: [2](#0-1) 

Both `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` are registered with `noopFormatChecker`, which accepts any `uint64` value: [3](#0-2) 

The merged `GovModule.GetParamSet` simply overlays contractgov's partial set on top of headergov's, with no re-validation of consistency between the two fields: [4](#0-3) 

This merged `ParamSet` (via `ToKip71Config()`) is the single source used both by block proposers to compute the next base fee and by validators to verify it: [5](#0-4) [6](#0-5) 

Inside `NextMagmaBlockBaseFee`, if `LowerBoundBaseFee > UpperBoundBaseFee`, the initial clamping logic (`makeEvenByCeil`/`makeEvenByFloor` and the parent-baseFee clamp) does not guard against this inverted range: [7](#0-6) 

Since the votes are stored as bytes in the `GovParam` contract's per-block-activation storage (KIP-81) with no requirement that both bounds be set together or in a consistent order, a single parameter update (e.g., only `upperboundbasefee` lowered below the currently effective `lowerboundbasefee`, or vice versa) can push the pair into an inconsistent state which is silently accepted by contract governance ingestion.

### Impact Explanation
An inverted bound (`lower > upper`) drives `NextMagmaBlockBaseFee` into a degenerate state: depending on the branch taken (parentGasUsed vs gasTarget comparisons and the `Cmp` clamps), the computed base fee can become fixed at an artificially low or artificially high value, or oscillate incorrectly relative to network congestion, defeating the KIP-71 pricing mechanism (loss of the fee-burn/anti-spam economics) and this same computation is used for `TxPool` gas-price floor enforcement (`blockchain/tx_pool.go` line 576) and mempool admission (`FilterTransactionWithBaseFee`), so it directly affects fee/pricing correctness enforced on every submitted transaction.

### Likelihood Explanation
This requires the KIP-81 on-chain governance flow (GovParam contract, controlled by council members per the contract's own access rules) to push conflicting values for the two related parameters, which is plausible either through misconfiguration or malicious council coordination, since there is no on-chain guard preventing it, unlike the parallel header-vote path which explicitly guards against it.

### Recommendation
Add the same `LowerBoundBaseFee <= UpperBoundBaseFee` consistency check to the contract-governance ingestion path — either in `contractGovModule.GetParamSet` immediately after parsing parameters, or centrally in `GovModule.GetParamSet` after merging headergov and contractgov partial sets, so that an inconsistent pair from either source is rejected/reset to defaults rather than silently propagated into `ToKip71Config()`.

### Proof of Concept
1. Enable Kore hardfork and configure a `GovParamContract` address in header governance (`governance.govparamcontract`).
2. Via the GovParam contract's KIP-81 `setParamIn`/`setParam` interface, submit `kip71.lowerboundbasefee = 750000000000` (current default upper bound) with an activation block, without correspondingly raising `kip71.upperboundbasefee`.
3. At the activation block, `contractGovModule.GetParamSet` returns `LowerBoundBaseFee = 750000000000` while `UpperBoundBaseFee` remains at its prior lower value (e.g. `25000000000`), since no consistency check runs (contrast with `kaiax/gov/headergov/impl/header.go` lines 188-201, which would have rejected this via a header vote).
4. `GovModule.GetParamSet` merges this inconsistent contract value on top of the default/headergov set (`kaiax/gov/impl/getter.go`).
5. Both the block proposer (`work/worker.go`) and validators (`blockchain/block_validator.go`) call `pset.ToKip71Config().NextMagmaBlockBaseFee(...)`/`VerifyMagmaHeader` with `LowerBoundBaseFee > UpperBoundBaseFee`, producing a base fee inconsistent with the network's intended KIP-71 congestion pricing.

### Citations

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

**File:** kaiax/gov/param.go (L335-367)
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

**File:** kaiax/gov/impl/getter.go (L1-37)
```go
package impl

import (
	"github.com/kaiachain/kaia/kaiax/gov"
)

func (m *GovModule) GetParamSet(blockNum uint64) gov.ParamSet {
	ret := gov.GetDefaultGovernanceParamSet()

	p0 := m.Fallback
	for k, v := range p0 {
		err := ret.Set(k, v)
		if err != nil {
			logger.CritWithStack("Failed to add param from Fallback", "name", k, "value", v, "error", err)
		}
	}

	p1 := m.Hgm.GetPartialParamSet(blockNum)
	for k, v := range p1 {
		err := ret.Set(k, v)
		if err != nil {
			logger.CritWithStack("Failed to add param from HeaderGov", "name", k, "value", v, "error", err)
		}
	}

	if m.isKoreHF(blockNum) {
		p2 := m.Cgm.GetPartialParamSet(blockNum)
		for k, v := range p2 {
			err := ret.Set(k, v)
			if err != nil {
				logger.CritWithStack("Failed to add param from ContractGov", "name", k, "value", v, "error", err)
			}
		}
	}

	return *ret
}
```

**File:** work/worker.go (L378-383)
```go
	if self.config.IsMagmaForkEnabled(nextBlockNum) {
		// NOTE-Kaia NextBlockBaseFee needs the header of parent, self.chain.CurrentBlock
		// So above code, TxPool().Pending(), is separated with this and can be refactored later.
		pset := self.govModule.GetParamSet(nextBlockNum.Uint64())
		nextBaseFee = pset.ToKip71Config().NextMagmaBlockBaseFee(parent.Number(), parent.Header().BaseFee, parent.GasUsed())
		pending = types.FilterTransactionWithBaseFee(pending, nextBaseFee)
```

**File:** blockchain/block_validator.go (L202-213)
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
	} else if header.BaseFee != nil {
		return ErrInvalidBaseFee
	}
```

**File:** params/kip71_config.go (L58-86)
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
```
