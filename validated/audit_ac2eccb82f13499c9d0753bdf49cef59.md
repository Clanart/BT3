## Analysis

The KIP‑71 dynamic base‑fee bounds (`LowerBoundBaseFee` / `UpperBoundBaseFee`) are governance parameters that are validated only against each other, with no sane absolute maximum, exactly mirroring the reported `AsyncVault.bounds.lower` misconfiguration pattern (a bound value that is technically within its declared type range but can be set to an extreme value that forces the affected calculation to an economically absurd result for every ordinary participant).

### Title
Missing Sanity Cap on KIP-71 `LowerBoundBaseFee` Allows Forced Excessive Fee Extraction From Every Transaction Sender - (File: kaiax/gov/headergov/impl/header.go)

### Summary
`checkConsistency` in the header-governance module only verifies that `LowerBoundBaseFee <= UpperBoundBaseFee` (and vice versa) when a governance vote updates either KIP-71 bound. [1](#0-0)  There is no upper sanity limit on `LowerBoundBaseFee` itself, and the parameter's `FormatChecker` is a no-op that accepts any `uint64` value. [2](#0-1)  This is structurally identical to the reported `AsyncVault` bug: a bound parameter is checked only for internal consistency (`lower <= upper`) but not against a reasonable ceiling that reflects protocol expectations, letting the owner/governing entity push the effective floor toward the maximum allowed range.

### Finding Description
`NextMagmaBlockBaseFee` guarantees the resulting base fee is never lower than `kc.LowerBoundBaseFee`, clamping the parent's base fee up to this floor before any target/usage-based adjustment is applied. [3](#0-2)  If `LowerBoundBaseFee` is voted to a value close to `UpperBoundBaseFee` (which the consistency check in `checkConsistency` permits, since it only rejects `LowerBoundBaseFee > UpperBoundBaseFee`), every subsequent block's base fee is forced to that inflated floor regardless of actual network gas usage, exactly as the reported vault issue forced `bounds.lower` toward its 1e18 ceiling to force a "hold back 99%" scenario. [4](#0-3)  The `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` parameters accept any `uint64` via `uint64Canonicalizer` with a no-op format checker, so there is no protocol-level ceiling analogous to the "restrict bounds.lower to 0.1e18" recommendation from the report. [2](#0-1) 

### Impact Explanation
Because base fee applies uniformly to every transaction submitted to the network (an unprivileged, permissionless action), an extreme `LowerBoundBaseFee` forces all senders to pay a base fee far above what network congestion justifies, extracting value from every transaction sender analogous to the vault's underpayment to redeemers. This is a network-wide fee-abuse condition reachable by any public-RPC transaction submitter, once the parameter is set.

### Likelihood Explanation
Governance parameter votes are restricted to permissioned voters/governing node, so triggering requires a misconfiguration or malicious vote by that privileged party — the same precondition as the original report ("owner sets bounds.lower to a high value"). Given the check only compares the two bounds to each other and never against a fixed ceiling, an honest but careless configuration (or a compromised governing node) can realistically produce this state, matching the report's "Medium" severity framing.

### Recommendation
Add an absolute sanity ceiling for `Kip71LowerBoundBaseFee` (and a floor for `Kip71UpperBoundBaseFee`) in `checkConsistency`/`Params[...].FormatChecker`, independent of their relative comparison, so governance cannot force the base fee to an economically unreasonable floor. [1](#0-0) 

### Proof of Concept
1. Submit a governance vote (as the governing node) setting `kip71.lowerboundbasefee` to a value just below the current `UpperBoundBaseFee` (e.g. 700000000000, with default upper bound 750000000000).
2. `checkConsistency` accepts the vote because `LowerBoundBaseFee <= UpperBoundBaseFee` holds. [4](#0-3) 
3. From the next epoch, `NextMagmaBlockBaseFee` clamps every block's base fee to at least this new floor regardless of gas usage. [5](#0-4) 
4. Every ordinary transaction sender is now forced to pay a base fee near the protocol maximum, independent of actual demand — the fee-side analog of the vault's forced 99% withholding.

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
