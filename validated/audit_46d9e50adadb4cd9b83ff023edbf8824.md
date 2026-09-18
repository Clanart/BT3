### Title
Missing decimals propagation in CW20-to-ERC20 pointer creation causes ERC20 `decimals()` to not reflect the underlying CW20 token's decimals - ([File: precompiles/pointer/pointer.go])

### Summary
The bug class from the external report — a token remapping/pointer path that fails to validate or forward the correct decimal precision between two representations of the same asset — has an analog in `AddCW20` in `precompiles/pointer/pointer.go`. This function creates an ERC20 pointer contract for a CW20 token but discards the `decimals` field returned by the CW20 `token_info` query and never forwards a decimals value to the pointer contract's constructor, unlike the sibling `AddNative` path, which correctly extracts and forwards `decimals` from bank denom metadata.

### Finding Description
`AddCW20` queries the underlying CW20 contract's `token_info` and only extracts `name` and `symbol` from the response, ignoring `decimals`: [1](#0-0) 

This is then passed into `UpsertERCCW20Pointer`, whose `utils.ERCMetadata` argument has no `Decimals` field set: [2](#0-1) 

`UpsertERCCW20Pointer` in the keeper forwards only `cw20Addr, metadata.Name, metadata.Symbol` as constructor arguments — no decimals value is passed to the deployed pointer contract at all: [3](#0-2) 

Contrast this with `UpsertERCNativePointer`, which explicitly threads `metadata.Decimals` through to the native pointer contract's constructor: [4](#0-3) 

And with `AddNative` itself, which carefully derives `decimals` from the bank denom metadata's `DenomUnits` before constructing the pointer: [5](#0-4) 

Since CW20 tokens on Sei can have arbitrary decimals (commonly 6, but also 18 or other values depending on the CW20 contract), and the CW20 pointer contract is never told the real decimals value, any `decimals()` value exposed by the deployed `CW20ERC20Pointer.sol` contract is decoupled from the actual underlying CW20 token's decimal precision.

### Impact Explanation
Any unprivileged EVM caller can invoke `addCW20Pointer` on the pointer precompile (`0x000000000000000000000000000000000000100b`) for an arbitrary CW20 contract address — this is a public, permissionless entry point reachable via a single EVM transaction. Once a mismatched-decimals pointer exists, any downstream EVM contract or DeFi integration (AMMs, lending protocols, price oracles) that reads `decimals()` from the pointer ERC20 to normalize balances/amounts will compute incorrect token quantities relative to the real CW20 balance backing it — exactly the same class of miscalculation described in the source report (a wrong scaling factor applied to value/amount calculations). This can lead to mispriced trades, incorrect collateral valuation, or other fund-loss scenarios for any contract that trusts the pointer's `decimals()` to match the underlying asset's actual precision.

### Likelihood Explanation
Likelihood is high in the sense that triggering pointer creation requires no privilege — it's a normal EVM contract call available to any address. However, actual fund-loss impact is contingent on there existing a CW20 token whose real decimals differ from whatever the pointer contract hardcodes/defaults to, and on a third-party DeFi contract trusting `decimals()` from the pointer without independent verification. The `AddCW20`/`UpsertERCCW20Pointer` code path itself is unconditionally reachable, but the actual bytecode/constructor of the `CW20ERC20Pointer` contract (in `contracts/src/CW20ERC20Pointer.sol`) was not fully inspected in this session, so it is uncertain whether the pointer contract dynamically queries decimals from the CW20 contract at call time (which would mitigate this) versus relying solely on the constructor argument (which is never supplied here, making the finding concrete).

### Recommendation
In `AddCW20` (`precompiles/pointer/pointer.go`), extract `decimals` from the `token_info` query response the same way `name`/`symbol` are extracted, and thread it through `UpsertERCCW20Pointer` → `UpsertERCPointer` as a constructor argument to the `cw20` pointer contract, mirroring the pattern already used for `AddNative`/`UpsertERCNativePointer`. Additionally verify that `CW20ERC20Pointer.sol`'s `decimals()` getter actually consumes this value (or dynamically queries the CW20 contract) rather than defaulting/hardcoding an unrelated value.

### Proof of Concept
1. Deploy or identify a CW20 contract whose `token_info` query reports `decimals: 18` (or any value other than what the pointer contract defaults to).
2. Call `addCW20Pointer(cw20Address)` on the pointer precompile at `0x000000000000000000000000000000000000100b` from any EOA — no special permission is required (see `AddCW20`).
3. Inspect the deployed pointer contract's `decimals()` return value and compare it against the CW20 token's actual `token_info().decimals` — the values will not correspond because the decimals value was never passed to the pointer contract's constructor (see `UpsertERCCW20Pointer`).
4. Any external contract that reads `decimals()` from the pointer to compute a scaled amount (e.g., `amount * 10**erc20.decimals()`) will compute a value inconsistent with the real CW20 token supply/precision, causing scaling errors up to several orders of magnitude depending on the decimals delta.

### Citations

**File:** precompiles/pointer/pointer.go (L106-124)
```go
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
```

**File:** precompiles/pointer/pointer.go (L146-159)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}
```

**File:** x/evm/keeper/pointer_upgrade.go (L49-57)
```go
func (k *Keeper) UpsertERCNativePointer(
	ctx sdk.Context, evm *vm.EVM, token string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "native", []interface{}{
			token, metadata.Name, metadata.Symbol, metadata.Decimals,
		}, k.GetERC20NativePointer, k.SetERC20NativePointer,
	)
}
```

**File:** x/evm/keeper/pointer_upgrade.go (L59-67)
```go
func (k *Keeper) UpsertERCCW20Pointer(
	ctx sdk.Context, evm *vm.EVM, cw20Addr string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "cw20", []interface{}{
			cw20Addr, metadata.Name, metadata.Symbol,
		}, k.GetERC20CW20Pointer, k.SetERC20CW20Pointer,
	)
}
```
