## Title
Type confusion in CW↔EVM pointer reverse-registry: Bech32 CW addresses are truncated to raw 20-byte "EVM addresses," allowing address collisions that break pointer identity guarantees - (File: `x/evm/keeper/pointer.go`)

### Summary
The Envoy bug is a type-confusion where one identity type (e.g. an email/URI SAN) is silently reinterpreted and matched as if it were a different identity type (a DNS name), letting an attacker's certificate be accepted for a domain it was never issued for. `x/evm/keeper/pointer.go` contains an analogous type confusion: whenever it needs to key the pointer "reverse registry" or check the pointer-to-pointer invariant for a CosmWasm (bech32) address, it does not treat the CW address as the variable-length bech32 string it is. Instead it calls `common.BytesToAddress([]byte(addr))`, which silently reinterprets/truncates the ASCII bech32 string into a fixed 20-byte EVM-style address, keeping only the trailing 20 raw bytes of the string.

### Finding Description
`common.BytesToAddress` (go-ethereum) right-truncates its input to the last 20 bytes when the input is longer than 20 bytes. Sei bech32 CW contract addresses are ~43 ASCII characters, so this call throws away the first ~23 characters of the address and derives an "EVM address" purely from the last 20 ASCII characters of the bech32 string.

This transformation is used as the *sole* identity key for:
- `cwAddressIsPointer`, which gates the "cannot create a pointer to a pointer" invariant for every EVM→CW pointer creation path (`SetERC20NativePointerWithVersion`, `SetERC20CW20PointerWithVersion`, `SetERC721CW721PointerWithVersion`, `SetERC1155CW1155PointerWithVersion`): [1](#0-0) 
- The reverse-registry entries written whenever a CW→ERC pointer is registered (`SetCW20ERC20PointerWithVersion`, `SetCW721ERC721PointerWithVersion`, `SetCW1155ERC1155PointerWithVersion`): [2](#0-1) 
- The pointee-lookup helpers used by pointer-management precompiles (`GetERC20Pointee`, `GetERC721Pointee`, `GetERC1155Pointee`): [3](#0-2) 

Because the derived key depends only on the last 20 ASCII bytes of the bech32 string, two *different* CosmWasm contract addresses that happen to share the same trailing 20 characters produce an identical `PointerReverseRegistryKey`. CosmWasm contract addresses are deterministic functions of `(creator, code_id, label/salt)`, and `Instantiate2`-style instantiation lets any unprivileged wasm user freely choose the salt used to derive their contract's address. An attacker can therefore grind salts off-chain (cheaply, since instantiation address derivation can be computed without submitting a transaction) to find a CW20/CW721/CW1155 contract address whose last 20 ASCII characters collide with an already-registered pointer's reverse-registry key, without needing to match the entire 43-character address.

### Impact Explanation
A collision lets an attacker:
1. Bypass the `ErrorPointerToPointerNotAllowed` invariant (`x/evm/keeper/pointer.go:26,35-36,70-71,105-106,140-141,175-176,220-221,255-256`) by presenting a *different* CW contract whose derived key aliases an existing pointer, corrupting the registry's one-to-one pointer/pointee assumption.
2. Cause `GetERC20Pointee`/`GetERC721Pointee`/`GetERC1155Pointee` (used by the pointer/pointer-view precompiles to resolve which CW contract a given ERC pointer represents, and vice versa) to resolve to the *wrong* contract, since the reverse lookup is keyed on the truncated/aliased identity rather than the actual bech32 address.
3. This breaks the identity binding that the CW↔EVM pointer bridge (`precompiles/pointer`) and downstream ERC20/ERC721/ERC1155 pointer contracts rely on to route balance/ownership operations to the correct underlying CW20/CW721/CW1155 contract — enabling token/asset confusion between two unrelated CW contracts, which can manifest as unauthorized transfer or accounting corruption through the pointer precompiles (matches the "unauthorized transfer via precompile or pointer" impact category).

### Likelihood Explanation
CosmWasm contract instantiation with attacker-chosen labels/salts is a fully permissionless, unprivileged action (`MsgInstantiateContract`/`MsgInstantiateContract2`), and pointer creation (`AddCW20`/`AddCW721`/`AddCW1155` in `precompiles/pointer`) is likewise callable by any EVM account. Because the collision search is over only 20 trailing ASCII characters of a bech32 string (not a full 160-bit uniformly random hash), and instantiation-address derivation can be precomputed off-chain for free before submitting any transaction, an attacker can search for a colliding suffix against any of the growing set of already-registered pointer contracts at comparatively low cost, then submit a single on-chain instantiate + `AddCW20`/`AddCW721`/`AddCW1155` call to exploit it. This is a Medium-likelihood, deterministic, single-transaction-reachable bug class.

### Recommendation
Never reinterpret a variable-length bech32 CW address as a fixed 20-byte EVM address via raw truncation. Use a collision-resistant derivation (e.g. `crypto.Keccak256([]byte(cwAddr))[12:]`, or keep separate keyspaces/prefixes for CW-derived vs. EVM-native reverse-registry entries) so that the reverse-registry key is injective with respect to distinct CW addresses, matching how `PointerCW20ERC20Key`/`PointerERC20CW20Key` etc. already store the full un-truncated address bytes.

### Proof of Concept
1. Attacker computes (off-chain, for free) many candidate `(code_id, salt)` pairs for `MsgInstantiateContract2` and derives the resulting predicted CW20 contract bech32 address for each, without broadcasting anything.
2. Attacker filters for a candidate whose derived address's **last 20 ASCII characters** equal the last 20 characters of an already-registered pointer's CW address (obtainable via `x/evm` pointer query endpoints or events emitted at `SetERC20CW20Pointer`/`SetCW721ERC721Pointer`/`SetCW1155ERC1155Pointer` time).
3. Attacker instantiates their CW20/CW721/CW1155 contract on-chain with the winning salt via `MsgInstantiateContract2`.
4. Attacker calls the pointer precompile's `AddCW20`/`AddCW721`/`AddCW1155` (`precompiles/pointer`) for their new contract; internally this calls `SetERC20CW20Pointer`/`SetERC721CW721Pointer`/`SetERC1155CW1155Pointer`, which key/read the reverse registry via `PointerReverseRegistryKey(common.BytesToAddress([]byte(addr)))` — colliding with the victim pointer's existing reverse-registry entry (`x/evm/keeper/pointer.go:174-183, 208-211`), corrupting the `cwAddressIsPointer`/`GetCW20Pointee`-family identity mapping between the two unrelated contracts.

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

**File:** x/evm/keeper/pointer.go (L208-211)
```go
func (k *Keeper) cwAddressIsPointer(ctx sdk.Context, addr string) bool {
	_, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))))
	return exists
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
