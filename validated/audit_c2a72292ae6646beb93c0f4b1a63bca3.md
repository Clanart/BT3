### Title
Reverse pointer-registry key derived by truncating attacker-controlled identifier strings to 20 bytes causes cross-asset key collisions in the CW↔EVM pointer registry - (File: `x/evm/keeper/pointer.go`)

### Summary
The current version of `AddNative` requires `token` to already have `bank` denom metadata [1](#0-0) , which limits pure denom-string abuse in the latest precompile, but the underlying keeper primitive it (and the CW20/CW721/CW1155 pointer setters) all call into still derives the reverse-registry lookup key by naively truncating/padding an attacker-influenceable string identifier to 20 bytes via `common.BytesToAddress([]byte(identifier))`, instead of hashing or exact-matching the full identifier — the same root-cause pattern as the Parse Server bug (an identifier that should be matched exactly is instead coerced/normalized in a lossy way before being used as a lookup key, letting unrelated inputs match).

### Finding Description
`PointerReverseRegistryKey` is meant to be a 1:1 reverse index from an EVM `common.Address` to the corresponding CW/native identifier (or vice versa), used by `evmAddressIsPointer` / `cwAddressIsPointer` to prevent "pointer-to-pointer" chains and by `GetCW20Pointee` / `GetCW721Pointee` / `GetCW1155Pointee` / `GetERC20Pointee` / `GetERC721Pointee` / `GetERC1155Pointee` to resolve cross-VM asset identity [2](#0-1) [3](#0-2) .

For the CW→ERC direction, the reverse key is computed not from the real 20-byte EVM contract address but from `common.BytesToAddress([]byte(addr))`, where `addr` is the pointee's bech32 CW address string (tens of bytes long) [4](#0-3) [5](#0-4) [6](#0-5) . `common.BytesToAddress` in go-ethereum right-truncates any input longer than 20 bytes, keeping only the last 20 bytes. Because a CW20/CW721/CW1155 bech32 address string is far longer than 20 bytes, this key is derived solely from its last 20 ASCII characters — an attacker who fully controls the identifier they register a pointer for (their own contract address, or historically an arbitrary native "token" string as accepted by `cwAddressIsPointer`/`evmAddressIsPointer`) can choose or search for an identifier whose trailing 20 characters exactly match those of a legitimate, already-registered pointer's identifier, producing an identical `PointerReverseRegistryKey`. `setPointerInfo` then writes to a version-indexed slot under that shared prefix key [7](#0-6) , so a colliding registration at the same pointer version overwrites the existing reverse-mapping entry rather than being rejected as a duplicate.

This is the direct analog of GHSA-5fw2-8jcv-xh87: instead of validating that the identifier is used in an *exact-match* comparison, the code applies a lossy transformation (truncation to 20 bytes) before using it as a store key, so operationally-distinct identifiers can be made to "match" the same stored record.

### Impact Explanation
The pointer registry's reverse mapping backs the guards that prevent creating a pointer-to-a-pointer (`ErrorPointerToPointerNotAllowed`) and backs the pointee-resolution functions used by the wasm↔EVM bridging code path. A collision lets an attacker either (a) silently overwrite the reverse-registry entry of a pre-existing, legitimate CW20/CW721/CW1155 pointer with data pointing at their own contract, corrupting `GetCW20Pointee`/`GetERC20Pointee`-style pointee resolution used by cross-VM asset bridging, or (b) spoof the "already a pointer" check to block or corrupt registration for a targeted denom/address. Because pointer contracts are the mechanism used to move value between the Cosmos/wasm bank layer and the EVM ERC20/721/1155 surface, corrupting this bidirectional index can misdirect asset-identity resolution used by transfer/bridging logic, which is a fund-integrity risk on the pointer bridge rather than a purely cosmetic bug.

### Likelihood Explanation
Exploitability depends on the caller's ability to choose an identifier string with an arbitrary/controlled 20-character (byte) suffix. This is straightforward for CW20/CW721/CW1155 registration since the pointee identifier is the caller-supplied bech32 address of a contract they must own/query (`AddCW20`/`AddCW721`/`AddCW1155` require a live `wasmd` contract at that address to answer `token_info`/`contract_info` queries) [8](#0-7) [9](#0-8) [10](#0-9) , so an attacker would still need to control (or grind, e.g. via CosmWasm `Instantiate2` salts) a contract address whose bech32 string ends in the target's trailing 20 characters — a nontrivial but not impossible search given bech32's constrained alphabet and salt-grinding tools. I was not able to fully confirm within this investigation whether current `AddNative`'s `GetDenomMetaData` gate fully eliminates the once-broader `cwAddressIsPointer`/`evmAddressIsPointer` abuse surface for arbitrary native tokens, since those two guard functions still perform the truncating derivation regardless of caller-supplied string length.

### Recommendation
Replace the truncating `common.BytesToAddress([]byte(identifier))` derivation used for `PointerReverseRegistryKey` lookups on CW/native string identifiers with a collision-resistant, length-prefixed or hashed encoding (e.g., a fixed-size hash of the full identifier, or a length-prefixed key as already used elsewhere in the codebase, such as `address.MustLengthPrefix`) so that distinct identifiers can never map to the same store key, and add an explicit check that rejects registration if the freshly computed reverse key already maps to a *different* stored identifier than the one being registered.

### Proof of Concept
Conceptual PoC (not fully executed/verified against a live node due to tool limitations):
1. Identify a legitimately registered CW20 pointer for contract address `cwAddrVictim` (bech32 string), which stores `PointerReverseRegistryKey(common.BytesToAddress([]byte(cwAddrVictim)))` → `cwAddrVictim` at some pointer version `v` [11](#0-10) .
2. Instantiate (or select) a CW20/CW721/CW1155 contract the attacker controls whose bech32 address `cwAddrAttacker` shares the exact same last-20 raw bytes as `cwAddrVictim` (feasible via CosmWasm `Instantiate2` salt selection/grinding).
3. Call `pointer.addCW20Pointer(cwAddrAttacker)` (or the CW721/CW1155 equivalent) at the same pointer version `v`; `SetERC20CW20PointerWithVersion` computes the identical `PointerReverseRegistryKey` and overwrites the victim's reverse-mapping entry via `setPointerInfo` [7](#0-6) .
4. Subsequent calls to `GetCW20Pointee`/`GetERC20Pointee` (or `cwAddressIsPointer`) for the victim's address now resolve using the attacker-supplied colliding data, demonstrating the exact-match failure.

### Citations

**File:** precompiles/pointer/pointer.go (L106-110)
```go
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
```

**File:** precompiles/pointer/pointer.go (L134-164)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
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

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/pointer/pointer.go (L166-196)
```go
func (p PrecompileExecutor) AddCW721(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/pointer/pointer.go (L198-228)
```go
func (p PrecompileExecutor) AddCW1155(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** x/evm/keeper/pointer.go (L69-78)
```go
func (k *Keeper) SetERC20CW20PointerWithVersion(ctx sdk.Context, cw20Address string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, cw20Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(cw20Address), version)
}
```

**File:** x/evm/keeper/pointer.go (L182-182)
```go
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc20Address[:], version)
```

**File:** x/evm/keeper/pointer.go (L203-211)
```go
func (k *Keeper) evmAddressIsPointer(ctx sdk.Context, addr common.Address) bool {
	_, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(addr))
	return exists
}

func (k *Keeper) cwAddressIsPointer(ctx sdk.Context, addr string) bool {
	_, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))))
	return exists
}
```

**File:** x/evm/keeper/pointer.go (L227-227)
```go
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc721Address[:], version)
```

**File:** x/evm/keeper/pointer.go (L262-262)
```go
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc1155Address[:], version)
```

**File:** x/evm/keeper/pointer.go (L341-347)
```go
func (k *Keeper) setPointerInfo(ctx sdk.Context, pref []byte, addr []byte, version uint16) error {
	store := prefix.NewStore(ctx.KVStore(k.GetStoreKey()), pref)
	versionBz := make([]byte, 2)
	binary.BigEndian.PutUint16(versionBz, version)
	store.Set(versionBz, addr)
	return nil
}
```

**File:** x/evm/keeper/pointer.go (L420-442)
```go
func (k *Keeper) GetERC20Pointee(ctx sdk.Context, cw20Address string) (erc20Address common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(cw20Address))))
	if exists {
		erc20Address = common.BytesToAddress(addrBz)
	}
	return
}

func (k *Keeper) GetERC721Pointee(ctx sdk.Context, cw721Address string) (erc721Address common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(cw721Address))))
	if exists {
		erc721Address = common.BytesToAddress(addrBz)
	}
	return
}

func (k *Keeper) GetERC1155Pointee(ctx sdk.Context, cw1155Address string) (erc1155Address common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(cw1155Address))))
	if exists {
		erc1155Address = common.BytesToAddress(addrBz)
	}
	return
}
```
