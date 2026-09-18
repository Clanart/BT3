### Title
`Bank` precompile's `decimals()` hardcodes `0` for every denom, diverging from the real exponent reported by the same denom's `denomMetadata()`/native ERC20 pointer, causing token value miscalculation for any consumer contract - ([File: precompiles/bank/bank.go])

### Summary
This is the analog of the Sentiment `ChainlinkOracle.getPrice()` bug: that bug hardcoded an assumption ("feed decimals == 8") that did not hold for every token, producing a value that was off by orders of magnitude and enabling fund drain. In sei-chain, the `Bank` precompile's `decimals(string denom)` method makes an analogous hardcoded assumption — that every native/tokenfactory denom is "integer-based" with `0` decimals — while the very same denom can carry a different, non-zero exponent in its `Metadata.DenomUnits` (queryable via `denomMetadata`/`denomsMetadata`), and that different, correct decimals value is what gets baked into the ERC20 pointer contract deployed for that same denom.

### Finding Description
`PrecompileExecutor.decimals()` in `precompiles/bank/bank.go` ignores its `denom` argument entirely and always packs `uint8(0)`: [1](#0-0) 

The Solidity interface exposes this as a per-denom query, `decimals(string memory denom) external view returns (uint8 response)`, implying callers can rely on it to interpret the raw integer amounts returned by `balance()`/`supply()`/`all_balances()` for arbitrary denoms: [2](#0-1) 

However, any tokenfactory denom creator (an unprivileged transaction sender) can set `DenomMetadata` for their denom with a non-zero exponent (e.g. 6 or 18) to mimic a standard ERC20/USDC-like token, and this metadata is fully queryable through the same precompile: [3](#0-2) [4](#0-3) 

Crucially, when a native ERC20 pointer contract is deployed for that same denom via the `pointer` precompile's `AddNative`, the pointer's on-chain `decimals` is computed from the denom's real metadata exponent (not hardcoded to 0): [5](#0-4) 

So for the exact same denom, two "authoritative" on-chain sources of `decimals` disagree:
- `IBank.decimals(denom)` → always `0` (confirmed even for a denom `"usei"` with real metadata name/symbol set) — verified by `TestMetadata`: [6](#0-5) 
- The deployed native ERC20 pointer contract for that denom → the actual max `DenomUnit.Exponent` from metadata (e.g., 6), as set during `UpsertERCNativePointer`.

This exact same hardcoded-`0` behavior is reproduced identically across essentially every historical precompile version (`v552` through the current version and every legacy snapshot in between), so it is not a one-off regression — it is a systemic assumption baked into the `Bank` precompile's ABI contract from the start: [7](#0-6) [8](#0-7) 

### Impact Explanation
Any on-chain (or off-chain) consumer contract that follows the standard ERC20-style pattern of calling `decimals()` to scale a raw balance into a human/USD-comparable value — the exact pattern from the original report where `RiskEngine._valueInWei` divides a raw price by `10 ** decimals` — will misinterpret amounts of any tokenfactory denom whose creator set a non-zero exponent (which is the norm for tokens meant to emulate standard decimal precision, e.g. 6 for USDC-like tokens, 18 for ETH-like tokens). Because `IBank.decimals()` unconditionally reports `0`, any protocol pricing collateral, computing swap ratios, or normalizing amounts through this call will treat, e.g., `1_000_000` raw units of a 6-decimal token as `1,000,000` whole tokens instead of `1` token — a 10^6 (or 10^18) overvaluation. This mirrors the Sentiment impact: an attacker/liquidity provider can deposit a trivially small raw amount of such a token and have it valued as if it were vastly larger, enabling under-collateralized borrowing or draining of a lending/AMM-style protocol built on the Sei EVM that trusts the `Bank` precompile's `decimals()` output. The bug is reachable purely through normal EVM contract calls (`eth_call`/transactions) to the `Bank` precompile at `0x1001` — no validator, governance, or privileged action required.

### Likelihood Explanation
High likelihood of being hit unintentionally, and straightforward to exploit deliberately: any tokenfactory denom creator (an ordinary transaction sender) can set arbitrary `DenomMetadata` with a non-zero exponent for their denom via the tokenfactory module, and any DeFi contract deployed on Sei EVM that naively calls `IBank(BANK_PRECOMPILE_ADDRESS).decimals(denom)` — following standard ERC20 integration patterns — will silently get the wrong scale. Because the discrepancy is invisible unless a developer specifically cross-checks against `denomMetadata()` or the pointer contract's `decimals()`, this is easy to miss during integration and audit.

### Recommendation
Make `Bank.decimals(denom)` consistent with the denom's actual metadata (mirroring what the `pointer` precompile's `AddNative` already computes from `DenomUnits`), rather than hardcoding `0`. At minimum:
- For denoms with registered `DenomMetadata`, return the maximum `DenomUnit.Exponent` (same logic already used in `precompiles/pointer/pointer.go#AddNative`) instead of a constant `0`.
- If no metadata exists, document explicitly that `0` is a fallback rather than a guarantee, and ensure downstream native ERC20 pointer generation and the `Bank` precompile's `decimals()` are derived from a single shared helper so they can never diverge for the same denom.

### Proof of Concept
1. As an unprivileged account, create a tokenfactory denom (e.g., `factory/<creator>/mytoken`) and call `SetDenomMetadata`/equivalent to set `DenomUnits` with a display unit at `Exponent = 6` (mirroring the pattern validated by `sei-cosmos/x/bank/types/metadata.go`'s `Validate()`).
2. Mint `1_000_000` raw units of this denom to the attacker's account (1.0 "display" token at 6 decimals).
3. Register a native ERC20 pointer for this denom via `pointer.addNativePointer(denom)` — the deployed pointer's `decimals()` will report `6` (per `precompiles/pointer/pointer.go#AddNative`, lines 99-127).
4. Deploy a naive lending/valuation contract that calls `IBank(BANK_PRECOMPILE_ADDRESS).decimals(denom)` (returns `0`, per `precompiles/bank/bank.go`, lines 399-407) to scale `IBank.balance(attacker, denom)` (`1_000_000`) into a "whole token" unit for pricing/collateral purposes: `amount / 10**decimals = 1_000_000 / 10**0 = 1_000_000` instead of the correct `1`.
5. The attacker's collateral/value is now overstated by 10^6×, allowing them to borrow/drain far more than their real deposit is worth — analogous to the AMPL/ETH decimals-mismatch drain in the original report.

Note: I was unable to independently confirm from the index alone whether any *first-party* sei-chain-shipped contract (as opposed to third-party integrators) currently consumes `IBank.decimals()` for value calculations; the vulnerability is demonstrated at the precompile/ABI-contract level, which is the reachable, in-scope surface per the rules (Cosmos precompiles, CW↔EVM pointers, tokenfactory denom creation). If a Devin session is needed to trace all first-party callers of `IBank.decimals()` across the full contracts/ and x/ trees, a full-repository session would be required since some file contents are excluded from this index.

### Citations

**File:** precompiles/bank/bank.go (L399-407)
```go
func (p PrecompileExecutor) decimals(ctx sdk.Context, method *abi.Method, _ []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	// all native tokens are integer-based, returns decimals for microdenom (usei)
	bz, err := method.Outputs.Pack(uint8(0))
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/bank/bank.go (L544-567)
```go
func (p PrecompileExecutor) denomMetadata(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	req := &banktypes.QueryDenomMetadataRequest{
		Denom: args[0].(string),
	}

	resp, err := p.bankQuerier.DenomMetadata(sdk.WrapSDKContext(ctx), req)
	if err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(convertMetadataToPrecompileType(resp.Metadata))
	if err != nil {
		return nil, 0, err
	}
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), nil
}
```

**File:** precompiles/bank/Bank.sol (L46-48)
```text
    function decimals(
        string memory denom
    ) external view returns (uint8 response);
```

**File:** sei-cosmos/x/bank/types/metadata.go (L42-60)
```go
	for i, denomUnit := range m.DenomUnits {
		// The first denomination unit MUST be the base
		if i == 0 {
			// validate denomination and exponent
			if denomUnit.Denom != m.Base {
				return fmt.Errorf("metadata's first denomination unit must be the one with base denom '%s'", m.Base)
			}
			if denomUnit.Exponent != 0 {
				return fmt.Errorf("the exponent for base denomination unit %s must be 0", m.Base)
			}
		} else if currentExponent >= denomUnit.Exponent {
			return errors.New("denom units should be sorted asc by exponent")
		}

		currentExponent = denomUnit.Exponent

		if seenUnits[denomUnit.Denom] {
			return fmt.Errorf("duplicate denomination unit %s", denomUnit.Denom)
		}
```

**File:** precompiles/pointer/pointer.go (L99-127)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
	if err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/bank/bank_test.go (L384-392)
```go
	decimal, err := p.ABI.MethodById(p.GetExecutor().(*bank.PrecompileExecutor).DecimalsID)
	require.Nil(t, err)
	args, err = decimal.Inputs.Pack("usei")
	require.Nil(t, err)
	res, _, err = p.RunAndCalculateGas(&evm, common.Address{}, common.Address{}, append(p.GetExecutor().(*bank.PrecompileExecutor).DecimalsID, args...), 100000, nil, nil, false, false)
	require.Nil(t, err)
	outputs, err = decimal.Outputs.Unpack(res)
	require.Nil(t, err)
	require.Equal(t, uint8(0), outputs[0])
```

**File:** precompiles/bank/legacy/v603/bank.go (L334-342)
```go
func (p PrecompileExecutor) decimals(ctx sdk.Context, method *abi.Method, _ []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	// all native tokens are integer-based, returns decimals for microdenom (usei)
	bz, err := method.Outputs.Pack(uint8(0))
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/bank/legacy/v620/bank.go (L335-343)
```go
func (p PrecompileExecutor) decimals(ctx sdk.Context, method *abi.Method, _ []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	// all native tokens are integer-based, returns decimals for microdenom (usei)
	bz, err := method.Outputs.Pack(uint8(0))
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```
