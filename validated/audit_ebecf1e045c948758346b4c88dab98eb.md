### Title
CW20/CW721/CW1155 pointer registry keys on raw un-normalized bech32 address strings, allowing duplicate pointer creation and bypass of the pointer-to-pointer guard - ([File: x/evm/keeper/pointer.go])

### Summary
The pointer registry (`x/evm/keeper/pointer.go`) and the `pointer` precompile (`precompiles/pointer/pointer.go`) key their forward/reverse lookup maps on the literal, caller-supplied bech32 address string rather than on the canonicalized (bech32-decoded) address bytes. Because bech32 is valid in both an all-lowercase and an all-uppercase form for the same underlying address, an attacker can register a second "unique" pointer for a CW20/CW721/CW1155 contract by resubmitting the identical Sei address with different letter casing, defeating both the duplicate-pointer check and the "cannot create a pointer to a pointer" safeguard.

### Finding Description
`AddCW20` (and the analogous `AddCW721`/`AddCW1155`/`RegisterPointer` flows) take the CW contract address as a raw `string` and use it directly, unmodified, as a map key: [1](#0-0) 

`GetERC20CW20Pointer`/`SetERC20CW20PointerWithVersion` store data keyed by `types.PointerERC20CW20Key(cw20Address)` where `cw20Address` is this same un-normalized string, and the reverse-lookup / anti-recursion guard `cwAddressIsPointer` also derives its key from the raw string bytes rather than from the canonical `sdk.AccAddress` bytes obtained via `sdk.AccAddressFromBech32`: [2](#0-1) [3](#0-2) 

Only the decoded `sdk.AccAddress` (via `sdk.AccAddressFromBech32(cwAddr)`) is used to actually query the underlying contract for `token_info`; the raw string is what gets persisted as the map key and as the constructor argument (`Cw20Address`) of the deployed ERC20 pointer contract: [4](#0-3) 

Because bech32 (per BIP-173, and as implemented by the underlying `github.com/cosmos/btcutil/bech32` library used in `GetFromBech32`/`AccAddressFromBech32`) accepts a string in either all-lowercase or all-uppercase form and decodes both to the exact same byte sequence, calling `addCW20Pointer("sei1abc...")` and later `addCW20Pointer("SEI1ABC...")` both decode to the same underlying CW20 contract address, yet they are treated as two distinct keys in the pointer registry: [5](#0-4) [6](#0-5) 

Consequences:
1. **Duplicate pointer creation**: The existing-pointer check `p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)` (keyed by the literal string) fails to detect an already-registered pointer if the case differs, so a second, functionally-independent ERC20 pointer contract can be deployed for the same underlying CW20 token.
2. **Bypass of the pointer-to-pointer guard**: `cwAddressIsPointer`/`evmAddressIsPointer` — the explicit safeguard represented by `ErrorPointerToPointerNotAllowed` — also rely on the same un-normalized string-derived key, so an already-registered pointer contract's address (in a different valid bech32 case form) can slip past the "cannot create a pointer to a pointer" check that the code otherwise intentionally enforces: [7](#0-6) 

### Impact Explanation
This breaks the codebase's own explicit invariant (`ErrorPointerToPointerNotAllowed`) that pointer chains must not be nested. Multiple independent ERC20/ERC721/ERC1155 pointer contracts (or a pointer that points to another pointer) referencing the same underlying CW20/CW721/CW1155/native asset create ambiguity about which pointer is "canonical" for downstream integrations (exchanges, other precompiles, wallets), fragmenting on-chain representation of a single fungible/non-fungible asset across multiple EVM addresses. Since pointer registration/deregistration and reverse-lookup logic elsewhere in the codebase assumes a 1:1 relationship between a CW address and its pointer, this can produce inconsistent state that a downstream integrator or precompile relying on `GetAnyPointeeInfo`/`GetCW20Pointee` could act on incorrectly, and nested/duplicate pointer chains are exactly the scenario the code's own guard was designed to prevent.

### Likelihood Explanation
Any unprivileged EVM caller can reach this: the `pointer` precompile's `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` methods are called directly by an EVM transaction sender with no special privileges, and constructing a valid uppercase bech32 variant of an existing address is trivial and always succeeds since it decodes to an identical checksum-valid address.

### Recommendation
Normalize the CW address (and any other bech32-derived registry key) to its canonical form before use as a map key, by decoding with `sdk.AccAddressFromBech32` and re-deriving the string via `.String()` (or storing the raw decoded bytes directly) prior to calling `GetERC20CW20Pointer`/`SetERC20CW20PointerWithVersion`/`cwAddressIsPointer`/`evmAddressIsPointer`, in `x/evm/keeper/pointer.go` and all the `AddCW20`/`AddCW721`/`AddCW1155` precompile handlers (current and legacy versions).

### Proof of Concept
1. Deploy/instantiate a CW20 contract at Sei address `sei1abc...xyz`.
2. Call `pointer.addCW20Pointer("sei1abc...xyz")` — this succeeds and creates ERC20 pointer contract `A`, registering the pointer under the literal lowercase string key.
3. Call `pointer.addCW20Pointer("SEI1ABC...XYZ")` (the same address, entirely uppercase, which is checksum-valid bech32 for the identical underlying bytes) — the existing-pointer check `GetERC20CW20Pointer(ctx, "SEI1ABC...XYZ")` misses the previously registered pointer under `"sei1abc...xyz"`, and a second, independent ERC20 pointer contract `B` is deployed and registered for the same CW20 contract.
4. Both `A` and `B` are now valid, functioning ERC20 pointer contracts for the same underlying CW20 token, and the reverse-lookup/anti-pointer-to-pointer checks (`cwAddressIsPointer`) can similarly be bypassed by supplying an already-pointer-registered address in an alternate case form.

Note: I was not able to fully trace the exact production consequence of an actual "pointer-to-a-pointer" chain (e.g., whether it can be leveraged for fund loss through repeated wrapping/unwrapping) within the available exploration; this would benefit from deeper tracing of `evm.Create`/`UpsertERCCW20Pointer` and the ERC20 pointer contract logic in a follow-up session with more iterations.

### Citations

**File:** precompiles/pointer/legacy/v552/pointer.go (L196-230)
```go
func (p Precompile) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	constructorArguments := []interface{}{
		cwAddr, name, symbol,
	}

	packedArgs, err := cw20.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(cw20.GetBin(), packedArgs...)
```

**File:** x/evm/keeper/pointer.go (L26-43)
```go
var ErrorPointerToPointerNotAllowed = sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "cannot create a pointer to a pointer")

// ERC20 -> Native Token
func (k *Keeper) SetERC20NativePointer(ctx sdk.Context, token string, addr common.Address) error {
	return k.SetERC20NativePointerWithVersion(ctx, token, addr, native.CurrentVersion)
}

// ERC20 -> Native Token
func (k *Keeper) SetERC20NativePointerWithVersion(ctx sdk.Context, token string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, token) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC20NativeKey(token), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(token), version)
}
```

**File:** x/evm/keeper/pointer.go (L63-96)
```go
// ERC20 -> CW20
func (k *Keeper) SetERC20CW20Pointer(ctx sdk.Context, cw20Address string, addr common.Address) error {
	return k.SetERC20CW20PointerWithVersion(ctx, cw20Address, addr, cw20.CurrentVersion(ctx))
}

// ERC20 -> CW20
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

// ERC20 -> CW20
func (k *Keeper) GetERC20CW20Pointer(ctx sdk.Context, cw20Address string) (addr common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), cw20.CurrentVersion(ctx))
	if exists {
		addr = common.BytesToAddress(addrBz)
	}
	return
}

// ERC20 -> CW20
func (k *Keeper) DeleteERC20CW20Pointer(ctx sdk.Context, cw20Address string, version uint16) {
	addr, _, exists := k.GetERC20CW20Pointer(ctx, cw20Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(addr), version)
	}
}
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

**File:** sei-cosmos/types/address.go (L167-186)
```go
// AccAddressFromBech32 creates an AccAddress from a Bech32 string.
func AccAddressFromBech32(address string) (addr AccAddress, err error) {
	if len(strings.TrimSpace(address)) == 0 {
		return AccAddress{}, errors.New("empty address string is not allowed")
	}

	bech32PrefixAccAddr := GetConfig().GetBech32AccountAddrPrefix()

	bz, err := GetFromBech32(address, bech32PrefixAccAddr)
	if err != nil {
		return nil, err
	}

	err = VerifyAddressFormat(bz)
	if err != nil {
		return nil, err
	}

	return AccAddress(bz), nil
}
```

**File:** sei-cosmos/types/bech32/bech32.go (L19-32)
```go
// DecodeAndConvert decodes a bech32 encoded string and converts to base64 encoded bytes.
func DecodeAndConvert(bech string) (string, []byte, error) {
	hrp, data, err := bech32.Decode(bech, 1023)
	if err != nil {
		return "", nil, fmt.Errorf("decoding bech32 failed: %w", err)
	}

	converted, err := bech32.ConvertBits(data, 5, 8, false)
	if err != nil {
		return "", nil, fmt.Errorf("decoding bech32 failed: %w", err)
	}

	return hrp, converted, nil
}
```
