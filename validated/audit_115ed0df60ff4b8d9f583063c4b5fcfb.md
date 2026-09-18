Confirmed: Sei contract addresses (`ContractAddrLen = 32`) are 32 bytes, but the CW↔ERC pointer reverse-registry key is computed by truncating the *bech32 string bytes* (not the decoded 32-byte address) down to a 20-byte `common.Address` via `common.BytesToAddress([]byte(addr))`. [1](#0-0) 

### Title
Pointer reverse-registry key collision from truncating CW contract bech32 strings enables cross-alias pointer metadata overwrite - ([File: x/evm/keeper/pointer.go])

### Summary
`SetCW20ERC20Pointer`, `SetCW721ERC721Pointer`, and `SetCW1155ERC1155Pointer` derive the reverse-registry key for a CW→ERC pointer by calling `common.BytesToAddress([]byte(addr))` on the CW contract's **bech32 string**, not its decoded 32-byte account address. [2](#0-1) [3](#0-2) [4](#0-3) 

`common.BytesToAddress` truncates its input to the last 20 bytes. Since `addr` here is the ASCII bech32 string of a 32-byte CW contract address rather than the raw 20/32-byte address, the resulting `common.Address` used as the reverse-registry key is only a function of the string's tail characters, discarding most of the entropy of the actual contract address. This same truncation is also used by `cwAddressIsPointer`/`evmAddressIsPointer`, the pointer-to-pointer guard. [5](#0-4) 

### Finding Description
This mirrors the Vault CVE-2022-40186 bug class: an identity/alias lookup keyed on the wrong (colliding) attribute causes metadata for one entity to be read/written under another entity's slot. Here, the "alias" is the CW contract's bech32-encoded address, and the registry key derivation ignores the true decoded address, instead hashing/truncating the printable string representation. Any CosmWasm user who can instantiate a contract (via `Instantiate`/`Instantiate2`, including `salt`-controlled `Instantiate2`) can grind for a contract address whose bech32 string shares the last 20 ASCII bytes with an already-registered legitimate pointer target. When that attacker-controlled contract is later passed to `RegisterPointer`/`SetCW20ERC20Pointer` (or the ERC1155/ERC721 equivalents), `setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), ...)` writes into the *same* KV slot as the victim's existing pointer's reverse entry, because `PointerReverseRegistryKey` is only 20 bytes wide and derived from the truncated string. [6](#0-5) 

This lets the attacker either overwrite the victim's reverse pointer info (corrupting `evmAddressIsPointer`/`cwAddressIsPointer` lookups used to block "pointer-to-pointer" registration) or spoof passing that guard so their own malicious contract is treated as a legitimate registered pointer counterpart.

### Impact Explanation
If the reverse-registry entry for a legitimate ERC20/CW20 (or 721/1155) pointer pair is overwritten, `cwAddressIsPointer`/`evmAddressIsPointer` checks used by `SetERC20CW20Pointer`, `SetCW721ERC721Pointer`, `SetCW1155ERC1155Pointer`, `SetCW20ERC20Pointer`, etc. can be bypassed or corrupted, potentially allowing an attacker to register their own pointer over/alongside a token that is actually backing real user balances via the pointer bridge, or to break the pointer-to-pointer protection that exists specifically to prevent double-wrapping tokens (a known vector for accounting/fund-safety bugs in the CW↔EVM bridge). This falls under "unauthorized transfer via precompile or pointer" / "fund loss" territory in the CW↔EVM pointer bridge that ordinary contract deployers and pointer users reach directly through public messages (`MsgRegisterPointer`, wasm `Instantiate2`).

### Likelihood Explanation
Exploitation requires the attacker to find a CosmWasm contract address (32 raw bytes, deterministic from creator+codeID+salt for `Instantiate2`) whose full bech32 string representation shares the same last 20 ASCII characters as an existing target's bech32 string. Because bech32 uses a 32-symbol alphabet, this is roughly a ~2^100 grinding search per target — computationally infeasible with realistic resources today. This significantly lowers real-world likelihood even though the code path is directly reachable by any unprivileged contract deployer/pointer registrant, and no additional privilege is required beyond submitting standard `Instantiate2`/`MsgRegisterPointer` transactions.

### Recommendation
Derive the reverse-registry key from the decoded raw account-address bytes of the CW contract (`sdk.AccAddressFromBech32(addr)` then pad/hash the full 32-byte value, e.g. via a length-prefixed or hashed key) rather than truncating the bech32 string's ASCII bytes. Apply the same fix to `evmAddressIsPointer`/`cwAddressIsPointer` and all six `Set*Pointer*` functions that compute `common.BytesToAddress([]byte(addr))` from a bech32 string.

### Proof of Concept
1. Register a legitimate pointer: call `RegisterPointer(PointerType_ERC20, ercAddress=<X>)`, which instantiates a CW20 pointer contract at address `V` (bech32 string `Sv`) and stores `PointerReverseRegistryKey(common.BytesToAddress([]byte(Sv))) -> X` [7](#0-6) .
2. An attacker repeatedly calls `wasmd`'s `Instantiate2` with different `salt` values to deterministically generate candidate contract addresses, computing each candidate's bech32 string and checking whether its last 20 ASCII bytes equal those of `Sv` (offline grinding, no on-chain cost until a match is found).
3. Once a colliding candidate contract address `W` (bech32 string `Sw`) is found, the attacker instantiates it and calls `AssociateContractAddress` or a pointer-set path such that `SetCW20ERC20Pointer(ctx, someErc20Address, Sw)` is invoked [7](#0-6) .
4. Because `common.BytesToAddress([]byte(Sw)) == common.BytesToAddress([]byte(Sv))`, the write to `PointerReverseRegistryKey(...)` overwrites the victim's reverse-pointer entry, corrupting the `evmAddressIsPointer`/`cwAddressIsPointer` guard check used to prevent pointer-to-pointer chains [5](#0-4) .

Note: I was unable to computationally verify an actual bech32-string-suffix collision within the scope of this review; the ~2^100 grinding cost referenced above is an estimate based on bech32's 5-bits-per-character encoding, not an empirically demonstrated collision.

### Citations

**File:** sei-wasmd/x/wasm/types/types.go (L19-22)
```go
	// ContractAddrLen defines a valid address length for contracts
	ContractAddrLen = 32
	// SDKAddrLen defines a valid address length that was used in sdk address generation
	SDKAddrLen = 20
```

**File:** x/evm/keeper/pointer.go (L168-183)
```go
// CW20 -> ERC20
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

**File:** x/evm/keeper/pointer.go (L213-228)
```go
// CW721 -> ERC721
func (k *Keeper) SetCW721ERC721Pointer(ctx sdk.Context, erc721Address common.Address, addr string) error {
	return k.SetCW721ERC721PointerWithVersion(ctx, erc721Address, addr, erc721.CurrentVersion)
}

// CW721 -> ERC721
func (k *Keeper) SetCW721ERC721PointerWithVersion(ctx sdk.Context, erc721Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc721Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW721ERC721Key(erc721Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc721Address[:], version)
}
```

**File:** x/evm/keeper/pointer.go (L248-263)
```go
// CW1155 -> ERC1155
func (k *Keeper) SetCW1155ERC1155Pointer(ctx sdk.Context, erc1155Address common.Address, addr string) error {
	return k.SetCW1155ERC1155PointerWithVersion(ctx, erc1155Address, addr, erc1155.CurrentVersion)
}

// CW1155 -> ERC1155
func (k *Keeper) SetCW1155ERC1155PointerWithVersion(ctx sdk.Context, erc1155Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc1155Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW1155ERC1155Key(erc1155Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc1155Address[:], version)
}
```

**File:** x/evm/types/keys.go (L182-186)
```go
func PointerReverseRegistryKey(addr common.Address) []byte {
	return append(PointerReverseRegistryPrefix, addr[:]...)
}


```
