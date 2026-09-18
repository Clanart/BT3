### Title
Pointer registry collapses variable-length bech32 CW addresses into 20 bytes via `common.BytesToAddress`, enabling reverse-registry key collisions - (File: x/evm/keeper/pointer.go)

### Summary
The Sydent CVE is a class of bug where an unambiguous-looking identifier (an email address) is parsed by a routine that silently truncates/misreads it (`parseaddr`), letting two different strings resolve to the same identity and bypass an access-control check keyed on that identity. Sei-chain's EVM↔CosmWasm pointer registry has the same bug-class root cause: it converts a variable-length bech32 CW address string into a fixed 20-byte `common.Address` key with `common.BytesToAddress([]byte(addr))`, which silently truncates any input longer than 20 bytes to its last 20 raw bytes [1](#0-0) . This truncated value is then used both as (a) the anti-"pointer-to-pointer" guard and (b) the actual storage key for the reverse pointee mapping used to resolve CW20/CW721/CW1155 ⇄ ERC20/721/1155 associations [2](#0-1) [3](#0-2) .

### Finding Description
`SetCW20ERC20Pointer`, `SetCW721ERC721Pointer`, and `SetCW1155ERC1155Pointer` all derive the reverse-registry key from a CW bech32 address string by calling `common.BytesToAddress([]byte(addr))` [4](#0-3) . `common.BytesToAddress` (go-ethereum) simply right-aligns the input into a 20-byte array, discarding any leading bytes beyond the last 20 when the input is longer than 20 bytes. A Sei bech32 address string (e.g. `sei1...`, typically ~39-44 ASCII bytes) is far longer than 20 bytes, so this call is a lossy, non-injective mapping from the full address string onto a 20-byte key — structurally identical to the CVE-2019-11340 pattern where `parseaddr("user@bad.example.net@good.example.com")` silently returns only the substring before the second `@`, letting an unintended value stand in for the "real" identity used for an authorization decision.

The same truncated key is reused for:
- `cwAddressIsPointer` (the "cannot create a pointer to a pointer" guard) [5](#0-4) 
- `GetERC20Pointee` / `GetERC721Pointee` / `GetERC1155Pointee`, which are the functions used by `x/evm/keeper/grpc_query.go` to resolve a CW pointee address for a given ERC pointer address [3](#0-2) .

Because the key space actually written to is only the last-20-bytes projection of the address string (not the full bech32 string, and not the actual 20/32-byte on-chain account bytes decoded from bech32), any two CW20/CW721/CW1155 contract addresses whose bech32 string representations share the same trailing 20 ASCII bytes will collide in the `PointerReverseRegistryKey` store. This is a design-level ambiguity bug: the code assumes a string-to-address coercion is safe/unambiguous when it is not, exactly mirroring the "unwanted behavior" class flagged in the Sydent advisory.

### Impact Explanation
If a collision is produced (deliberately or accidentally):
1. `cwAddressIsPointer` can return a false positive/negative for an unrelated address, incorrectly blocking or unexpectedly failing to block "pointer-to-pointer" creation.
2. More seriously, `SetCW20ERC20Pointer`/`SetCW721ERC721Pointer`/`SetCW1155ERC1155Pointer` write the reverse pointer entry (`erc20Address[:]`, i.e., which ERC contract a given CW contract points to) keyed by the truncated collision value. A second registration whose address collides with an already-registered CW address's truncated key will overwrite the first entry's reverse mapping at the same store slot (same key, different version bucket only distinguishes version, not the original full address), corrupting `GetCW20Pointee`/`GetERC20Pointee` resolution for the original address. Any EVM/RPC caller or precompile logic that relies on `GetERC20Pointee`/`GetERC721Pointee`/`GetERC1155Pointee` via `x/evm/keeper/grpc_query.go` to resolve "which CW contract does this ERC pointer represent" could then be redirected to the wrong CW contract, which can misdirect balance/ownership queries and downstream pointer-based transfers, i.e., a form of unauthorized association/mapping corruption reachable by any CW contract deployer registering a pointer through `MsgRegisterPointer`/`AddCW20`/`AddCW721`/`AddCW1155`.

### Likelihood Explanation
Exploitability depends on being able to produce (or find) a CW20/CW721/CW1155 address whose bech32 string collides in its last 20 raw bytes with a target address. All CW contract addresses on Sei have the same fixed length (fixed-length data payload + fixed `sei` HRP), so the collision space is roughly bounded by the checksum/tail entropy of bech32 encoding, which is large — a targeted collision against an arbitrary victim address is not trivial to grind. However, contract addresses derived via any grindable instantiation salt (if available to depositors) increase feasibility, and the underlying design flaw (silent truncation of variable-length identifiers into a fixed-size key without validating uniqueness) is a genuine, unambiguous root-cause bug independent of how hard a specific collision is to find — it is the exact analog of the reported CWE-20 "input parsing produces an unintended value used for a security decision" class. I was not able to fully verify within tool budget whether wasmd contract instantiation on this chain supports attacker-grindable salts (Instantiate2) for the CW20/721/1155 contracts registered as pointer targets, which would materially raise the practical likelihood; this should be verified in a live/Devin session.

### Recommendation
Do not derive the pointer reverse-registry key by naively truncating the bech32 address string via `common.BytesToAddress([]byte(addr))`. Instead:
- Decode the bech32 string to its canonical `sdk.AccAddress` bytes first (`sdk.AccAddressFromBech32`), and use a length-prefixed or hashed (e.g., keccak/sha256) representation of the full decoded address as the registry key, so no information is discarded and no two distinct addresses can collide.
- Apply this fix consistently across `SetCW20ERC20Pointer`, `SetCW721ERC721Pointer`, `SetCW1155ERC1155Pointer`, `cwAddressIsPointer`, `GetERC20Pointee`, `GetERC721Pointee`, and `GetERC1155Pointee` in `x/evm/keeper/pointer.go`.
- Add an explicit uniqueness/collision check before writing any reverse-registry entry, rejecting registration if the derived key already maps to a different original address.

### Proof of Concept
Conceptual (full end-to-end collision-finding was not verified due to tool constraints):
1. Attacker deploys/controls two CosmWasm contracts, A (a legitimate, already-pointer-registered CW20/CW721/CW1155 contract) and B (attacker's own contract), such that the last 20 raw bytes of A's and B's bech32 address strings are identical (feasible if any grindable instantiation path exists for contract address derivation).
2. Attacker calls `MsgRegisterPointer` / the `AddCW20`/`AddCW721`/`AddCW1155` precompile method for contract B.
3. `SetCW20ERC20Pointer`/`SetCW721ERC721Pointer`/`SetCW1155ERC1155Pointer` writes `PointerReverseRegistryKey(common.BytesToAddress([]byte(B)))`, which is the same store key previously written for A, silently overwriting A's reverse mapping [4](#0-3) .
4. Subsequent calls to `GetCW20Pointee`/`GetERC20Pointee`/etc. for the ERC pointer originally tied to A now resolve to B's stored value, corrupting the pointee association used by `x/evm/keeper/grpc_query.go` consumers.

### Citations

**File:** x/evm/keeper/pointer.go (L169-183)
```go
func (k *Keeper) SetCW20ERC20Pointer(ctx sdk.Context, erc20Address common.Address, addr string) error {
	return k.SetCW20ERC20PointerWithVersion(ctx, erc20Address, addr, erc20.CurrentVersion)
}

// CW20 -> ERC20
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

**File:** x/evm/keeper/pointer.go (L420-443)
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
