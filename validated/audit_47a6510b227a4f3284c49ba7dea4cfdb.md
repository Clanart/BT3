### Title
Division-by-zero panic via governance-settable `GasTarget=0` in KIP-71 base fee calculation - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the governance-controlled `GasTarget` parameter without checking for zero, unlike the adjacent `BaseFeeDenominator` field which is explicitly guarded against a zero value. The `governance.kip71.gastarget` parameter is registered in the governance parameter table with a no-op format checker that never rejects zero, so a governance vote (or single-governing-node parameter update) that sets `GasTarget` to `0` will cause every node computing the next block's base fee to panic with a division-by-zero, crashing block processing and RPC gas-price endpoints network-wide. This mirrors the FlashMQ CVE-2026-42209 pattern: a normally-privileged but reachable configuration knob, when set to specific non-default values, causes an unguarded integer division that crashes the process.

### Finding Description
`NextMagmaBlockBaseFee` explicitly protects against `BaseFeeDenominator == 0`: [1](#0-0) 

But `GasTarget` receives no equivalent protection, and is used directly as a divisor: [2](#0-1) [3](#0-2) 

If `parentGasUsed != gasTarget` (i.e., `gasTarget == 0` and any nonzero gas was used, or more generally whenever the two differ), the code reaches `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which is `math/big`'s `Int.Div` — this panics with `"division by zero"` when the divisor is a zero-valued `big.Int`, exactly the class of bug described in the external report (division by zero from a non-default configuration combination).

The `GasTarget` parameter is a normal governance-tunable value with no positivity/nonzero validation: [4](#0-3) 

Compare to `BaseFeeDenominator`'s format checker, which is likewise `noopFormatChecker` — the source-code-level guard for that field exists only inside `NextMagmaBlockBaseFee` itself, not in the parameter validation layer. `GasTarget` has no such runtime guard.

`NextMagmaBlockBaseFee` is invoked both during block header verification (`VerifyMagmaHeader`) and during public RPC fee estimation: [5](#0-4) [6](#0-5) 

This means once a block sets `GasTarget=0` via a governance vote, every full node — while executing `eth_feeHistory`/`kaia_getReward` RPC calls, or while validating/creating any subsequent block with nonzero gas usage — will panic and crash.

### Impact Explanation
A crash-inducing division by zero reachable through header verification and gas price computation affects every node that processes blocks after the malicious parameter takes effect, producing a chain-wide denial of service. This is consistent with the "acceptance of an invalid transaction or block" / "state divergence between honest nodes" impact criteria, since it halts block production/validation across the network rather than affecting a single node.

### Likelihood Explanation
The trigger requires a governance vote to set `governance.kip71.gastarget` to `0`. Depending on `GovernanceMode` ("single" mode requires only the governing node to submit the vote transaction), this can be a single governance-vote transaction submitted by an already-permissioned account — directly analogous to the FlashMQ bug's requirement that the attacker hold "the corresponding publish permission." Given the parameter validation performs no bounds checking (`noopFormatChecker`), nothing in the vote-processing or parameter-application pipeline prevents the value from being accepted.

### Recommendation
Add an explicit zero-guard for `GasTarget` in `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator == 0` fallback), and/or add a `FormatChecker` for `Kip71GasTarget` (and any other divisor-role KIP-71 parameters) in `kaiax/gov/param.go` that rejects `0` before the vote/parameter can be applied to `ChainConfig`.

### Proof of Concept
1. Governing node (or a quorum under general governance mode) submits a governance vote setting `governance.kip71.gastarget = 0`.
2. Once the vote takes effect at the target block, any subsequent block whose `GasUsed != 0` causes `NextMagmaBlockBaseFee` to execute `parentGasUsed > gasTarget` (since `gasTarget == 0`), reaching:
   `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`.
3. `math/big.Int.Div` panics with `"division by zero"`, crashing the node process for every node that verifies the header or serves `eth_feeHistory`.

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

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
