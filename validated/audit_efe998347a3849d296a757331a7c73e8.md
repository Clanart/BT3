### Title
Non-canonical, collidable pointer-registry key derivation from CosmWasm bech32 address strings allows registry collisions/overwrites - ([File: x/evm/keeper/pointer.go])

### Summary
`x/evm/keeper/pointer.go` derives the CW→EVM "reverse pointer registry" key from a CosmWasm contract address string by calling `common.BytesToAddress([]byte(addr))` on the raw ASCII bytes of the bech32 string, rather than on the decoded canonical address bytes. `common.BytesToAddress` (go-ethereum) is non-injective for inputs longer than 20 bytes: it keeps only the last 20 bytes of the input and silently discards the rest. Because CW20/CW721/CW1155 bech32 addresses are much longer than 20 bytes, this mirrors the Tink CVE-2020-8929 bug class: a lookup/registry key is derived through a lossy, non-canonical transformation of attacker-influenced bytes, so multiple distinct real values (here, distinct contract addresses) can be mapped to the identical registry key, enabling collisions that the code's uniqueness checks assume cannot happen.

### Finding Description
The pointer keeper stores a bidirectional EVM↔CW registry. For CW→ERC pointers, the reverse-registry key that both the collision guard (`cwAddressIsPointer`) and the value written under `PointerReverseRegistryKey` use is derived like this: [1](#0-0) 

and the read-side symmetric helpers: [2](#0-1) [3](#0-2) 

`common.BytesToAddress` right-aligns/truncates any input longer than 20 bytes to its last 20 bytes (go-ethereum `Address.SetBytes`), so `common.BytesToAddress([]byte(bech32AddrString))` is computed from the trailing 20 ASCII characters of the bech32 string — not from the 20/32-byte decoded contract address. Wasmd contract bech32 strings on Sei are well over 20 characters (as reflected in `sei-wasmd/x/wasm/types/keys.go`/`keys_test.go`, which support both 20- and 32-byte `ContractAddrLen` addresses whose bech32 encodings are long strings), so this transformation is lossy and not one-to-one: many different contract address strings can produce the same trailing-20-character suffix, and thus the same `PointerReverseRegistryKey`.

This directly parallels the Tink advisory's root cause: a security-relevant lookup key ("key ID"/prefix in Tink; "reverse pointer key" here) is built from a lossy string transformation of untrusted bytes instead of the canonical bytes, letting an attacker who controls the input construct a colliding key.

### Impact Explanation
The reverse-registry key backs two guarantees enforced everywhere pointers are created:
- `evmAddressIsPointer`/`cwAddressIsPointer` are used to prevent "pointer to a pointer" recursion (`ErrorPointerToPointerNotAllowed`), a check relied upon by `AddCW20`/`AddNative` pointer-registration precompile methods (e.g. [4](#0-3) ) to protect the pointer/pointee mapping invariants that the CW20/CW721/CW1155 pointer contracts (e.g. `contracts/src/CW20ERC20Pointer.sol`) rely on when routing `transfer`/`transferFrom`/`balanceOf` calls to the underlying CosmWasm contract via the Wasmd precompile.
- The same key also stores the actual pointee data (`k.setPointerInfo(ctx, types.PointerReverseRegistryKey(...), erc20Address[:], version)`), consulted by `GetERC20Pointee`/`GetCW721Pointee`/etc. (used from `x/evm/keeper/grpc_query.go`).

Because a CosmWasm contract deployer can freely choose the resulting contract address via CosmWasm's `Instantiate2` deterministic address derivation (choosing an arbitrary salt), an attacker can search for a salt that produces a CW20/CW721/CW1155 contract whose bech32 address string happens to end in the same 20 ASCII characters as an already-pointer-registered legitimate contract address. Once deployed, registering an ERC pointer for the attacker's contract writes to (overwrites) the exact same `PointerReverseRegistryKey` slot used by the pre-existing legitimate pointer's reverse mapping (same version bucket), because `setPointerInfo` keys entries only by a 2-byte version suffix under the collided prefix. This can corrupt or redirect the reverse pointee lookup that pointer contracts and pointee/precompile queries depend on, causing an ERC20/CW20 pointer's `Pointee`/reverse-registry entry to resolve to the wrong contract — a form of unauthorized redirection of pointer routing between unrelated CW20/CW721/CW1155 contracts, which can lead to fund-routing confusion (transfers/balances resolved against the wrong CosmWasm contract) or to the "pointer-to-pointer" collision guard being bypassed/falsely triggered, undermining the pointer registry's uniqueness invariant.

### Likelihood Explanation
Exploitability requires the attacker to brute-force a `Instantiate2` salt whose resulting bech32 contract address ends in a chosen 20-character suffix from within the bech32 charset (32 possible characters), which is a bounded, offline, unprivileged computation available to any CosmWasm user/contract deployer — no validator or node compromise is required. This is analogous in class (though not identical in mechanics) to the Tink advisory's requirement that an attacker manipulate byte sequences to hit a colliding lookup key. I was not able to fully verify, purely from static reading, the exact end-to-end reachability of a fund-loss scenario through the ERC pointer contract's Solidity logic in all pointer versions (multiple legacy pointer precompile versions exist: v552, v555, v562, v575, etc.), so likelihood of full fund-loss impact (versus a registry-corruption/DoS-type impact) should be validated with a concrete PoC deployment.

### Recommendation
Derive `PointerReverseRegistryKey` (and any similar CW-address-to-pseudo-EVM-address mapping) from the canonical decoded `sdk.AccAddress` bytes (via `sdk.AccAddressFromBech32`) rather than from `common.BytesToAddress([]byte(bech32String))`, or use a dedicated, injective key encoding (e.g., a distinct prefix plus the full bech32/canonical bytes) instead of truncating to `common.Address`'s 20-byte format. Apply this fix consistently across `SetCW20ERC20Pointer`, `SetCW721ERC721Pointer`, `SetCW1155ERC1155Pointer`, their `cwAddressIsPointer`/`evmAddressIsPointer` guards, and `GetERC20/721/1155Pointee`.

### Proof of Concept
Conceptual PoC (requires live-chain verification, not fully executed here):
1. Deploy a legitimate CW20 contract `A` and register it as an ERC20 pointer target via `AddCW20`, which stores `PointerReverseRegistryKey(common.BytesToAddress([]byte(A.String())))`.
2. Using CosmWasm `Instantiate2` with an attacker-chosen salt, brute-force a second CW20 contract `B` whose bech32 address string shares the same last 20 characters as `A.String()`.
3. Register `B` as another ERC20 pointer. `k.cwAddressIsPointer(ctx, B)` computes the same key as `A`'s reverse-registry entry, and/or `SetERC20CW20Pointer` overwrites the same reverse-registry slot used by `A`, corrupting `GetCW20Pointee`/`GetERC20Pointee` resolution for one of the two contracts. [2](#0-1)

### Citations

**File:** x/evm/keeper/pointer.go (L174-183)
```go
func (k *Keeper) SetCW20ERC20PointerWithVersion(ctx sdk.Context, erc20Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc20Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW20ERC20Key(erc20Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc20Address[:], version)
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

**File:** x/evm/keeper/pointer.go (L420-426)
```go
func (k *Keeper) GetERC20Pointee(ctx sdk.Context, cw20Address string) (erc20Address common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(cw20Address))))
	if exists {
		erc20Address = common.BytesToAddress(addrBz)
	}
	return
}
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L196-248)
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
	if value == nil {
		value = utils.Big0
	}
	ret, contractAddr, remainingGas, err := evm.Create(caller, bin, suppliedGas, uint256.MustFromBig(value))
	if err != nil {
		return
	}
	err = p.evmKeeper.SetERC20CW20Pointer(ctx, cwAddr, contractAddr)
	if err != nil {
		return
	}

	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "cw20"),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, cwAddr),
		sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", cw20.CurrentVersion(ctx)))))
	ret, err = method.Outputs.Pack(contractAddr)
	return
```
