<document>
<document_title>
Repo bsaldua/push-chain-evm--007: x/erc20/keeper/ibc_callbacks.go:217-254
</document_title>

<document_context>
Relevance score of this snippet (1-5 scale, higher is better: 1 = likely irrelevant, 5 = likely relevant): 5.00
</document_context>

<document_content>
  217|	case pair.IsNativeERC20():
  218|		// use a zero gas config to avoid extra costs for the relayers
  219|		ctx = ctx.
  220|			WithKVGasConfig(storetypes.GasConfig{}).
  221|			WithTransientKVGasConfig(storetypes.GasConfig{})
  222|
  223|		params := k.GetParams(ctx)
  224|		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
  225|			// no-op, ERC20s are disabled or the denom is not registered
  226|			return nil
  227|		}
  228|
  229|		// assume that all module accounts on Cosmos EVM need to have their tokens in the
  230|		// IBC representation as opposed to ERC20
  231|		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
  232|		if types.IsModuleAccount(senderAcc) {
  233|			return nil
  234|		}
  235|
  236|		// Convert from Coin to ERC20
  237|		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
</document_content></document>

### Supplying and borrowing can recreate p2p credit lines even if p2p is disabled - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
The `erc20` module fails to check the global `EnableErc20` parameter during the automatic conversion of native coins to ERC20 tokens in IBC acknowledgement and timeout callbacks. This allows users to re-enter ERC20 positions (recreating the "p2p" analog of asset representation) even after governance has disabled the module to force liquidity back into native Cosmos coins.

### Finding Description
In the Cosmos EVM `erc20` module, the `EnableErc20` parameter acts as a global kill-switch for conversions between Cosmos coins and ERC20 tokens [1](#0-0) . While the `MsgConvertCoin` and `MsgConvertERC20` entry points correctly enforce this check via `MintingEnabled` [2](#0-1) , the IBC callback logic for `OnAcknowledgementPacket` and `OnTimeoutPacket` bypasses it in a specific case [3](#0-2) .

Specifically, when a user sends a native ERC20 token via IBC and the packet fails (timeout or error), the module attempts to refund the user by converting the returned native coins back into their ERC20 representation via `ConvertCoinToERC20FromPacket` [4](#0-3) . However, this function only checks the `EnableErc20` parameter for "Case 2" (Native ERC20s) [5](#0-4) . 

If governance disables `EnableErc20` to prevent new ERC20 credit lines/representations and move liquidity to the bank module, an attacker or unlucky user can still trigger a conversion from Coin to ERC20 by inducing an IBC failure. This results in the creation of ERC20 tokens (minting or unescrowing) [6](#0-5)  at a time when the system invariant should strictly prohibit such state transitions.

### Impact Explanation
The impact is **Critical** accounting corruption and unauthorized asset representation. By bypassing the `EnableErc20` global safety switch, the system allows the "resurrection" of ERC20-denominated value that governance has explicitly intended to freeze or migrate [7](#0-6) . This breaks the fundamental invariant that module parameters must gate all state-mutating execution flows. In a scenario where an ERC20 contract is being deprecated due to a vulnerability, this bypass allows funds to be moved back into the vulnerable contract representation via IBC relaying.

### Likelihood Explanation
The likelihood is **Medium**. It requires governance to have disabled the `erc20` module (a state typically used during upgrades or emergencies) and an IBC packet to fail. An attacker can intentionally trigger this by sending an IBC transfer with a very short timeout to a non-existent destination chain, ensuring the `OnTimeoutPacket` logic executes on the source chain while the module is disabled.

### Recommendation
Update `ConvertCoinToERC20FromPacket` in `x/erc20/keeper/ibc_callbacks.go` to check `IsERC20Enabled` at the beginning of the function, ensuring no conversion logic is executed for any case if the module is disabled.

### Proof of Concept
1. Governance sets `EnableErc20 = false` in the `erc20` module params to disable all conversions [8](#0-7) .
2. An attacker identifies a `TokenPair` where the native asset is an ERC20 (e.g., a contract deployed on the EVM).
3. The attacker initiates an IBC transfer of the native Cosmos coin representation of that token.
4. The attacker ensures the IBC packet times out (e.g., by setting a 1-second timeout).
5. The `OnTimeoutPacket` handler is triggered, which calls `ConvertCoinToERC20FromPacket` [9](#0-8) .
6. `ConvertCoinToERC20FromPacket` proceeds to call `ConvertCoinNativeERC20` [10](#0-9) , effectively minting/unescrowing ERC20 tokens for the user despite the module being disabled.

### Citations

**File:** x/erc20/keeper/mint.go (L23-27)
```go
	if !k.IsERC20Enabled(ctx) {
		return types.TokenPair{}, errorsmod.Wrap(
			types.ErrERC20Disabled, "module is currently disabled by governance",
		)
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L172-188)
```go
		return k.ConvertCoinToERC20FromPacket(ctx, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing needs to
		// be executed and no error needs to be returned
		return nil
	}
}

// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L192-192)
```go
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
```

**File:** x/erc20/keeper/ibc_callbacks.go (L224-227)
```go
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L237-237)
```go
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
```

**File:** x/erc20/keeper/msg_server.go (L263-263)
```go
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
```

**File:** docs/migrations/v0.4.0_erc20_precompiles_migration.md (L15-21)
```markdown
**Known Issues if Not Migrated:**

- ERC20 balances will show as 0 when queried via EVM
- `totalSupply()` calls return 0
- Token transfers via ERC20 interface fail
- Native Cosmos balances remain intact but inaccessible via EVM

```

**File:** x/erc20/keeper/params.go (L32-39)
```go
func (k Keeper) setERC20Enabled(ctx sdk.Context, enable bool) {
	store := ctx.KVStore(k.storeKey)
	if enable {
		store.Set(types.ParamStoreKeyEnableErc20, isTrue)
		return
	}
	store.Delete(types.ParamStoreKeyEnableErc20)
}
```
