### Title
`CW721ERC721Pointer.ownerOf()` permanently reverts (freezing NFT transfers) when the underlying CW721 token owner has no EVM address association - (File: `contracts/src/CW721ERC721Pointer.sol`)

### Summary
The `CW721ERC721Pointer` contract exposes an ERC721 interface backed by a CosmWasm CW721 collection via the Wasmd/Addr precompiles. Its `ownerOf()` implementation unconditionally converts the CW721 owner's Sei bech32 address into an EVM address using `AddrPrecompile.getEvmAddr()`. This precompile call reverts with `"sei address %s is not associated"` whenever the current token owner has never performed an EVM address association. Because `transferFrom`, `tokenURI`, and any external caller/marketplace depend on `ownerOf()` succeeding, an NFT whose owner is an un-associated Sei address becomes permanently un-transferable and un-queryable through the ERC721 pointer surface, exactly mirroring the reported bug class where an unhandled lookup-failure edge case in a helper used by multiple claim/ownership functions causes a hard revert and asset freeze for legitimate holders.

### Finding Description
`ownerOf()` is implemented as: [1](#0-0) 

It queries the CosmWasm contract for `owner_of`, extracts the bech32 owner string, and calls `AddrPrecompile.getEvmAddr(string(owner_))` to resolve an EVM address for that native owner. The `getEvmAddr` precompile handler explicitly errors out (rather than returning a zero/default address) when the given Sei address has no EVM association: [2](#0-1) 

This differs from other places in the codebase that use `GetSeiAddressOrDefault`/`GetEVMAddressOrDefault` fallbacks (e.g. the `Claim`/`ClaimSpecific` precompile executor uses `GetSeiAddressOrDefault`) precisely to avoid hard failures when an association is missing. `CW721ERC721Pointer.ownerOf()` has no such fallback or try/catch, so the revert propagates directly.

Because `ownerOf()` reverts, every dependent pointer function reverts too:
- `transferFrom()` calls `require(from == ownerOf(tokenId), ...)` before transferring, so the NFT cannot be moved through the EVM pointer at all: [3](#0-2) 
- `tokenURI()` explicitly calls `ownerOf(tokenId)` first "to revert if token isn't owned": [4](#0-3) 

A CW721 token can easily end up owned by a Sei-native address with no EVM association — e.g. a native Sei wallet that minted/received the NFT purely through CosmWasm and never associated an EVM key, or a CosmWasm contract address (module accounts, vaults, DAOs) that by design has no EVM counterpart. Any EVM user who then interacts with the ERC721 pointer for that token (to view metadata, verify ownership as part of a marketplace/lending integration, or receive a transfer) will have their transaction unconditionally revert, with no way to work around it from the EVM side — matching the external report's core defect pattern: a shared "resolve owner" helper used by several downstream flows has an unhandled failure case that blocks all of them.

### Impact Explanation
Any CW721 collection pointed to by a `CW721ERC721Pointer` can contain tokens that are permanently frozen from the EVM perspective: `ownerOf`, `tokenURI`, and `transferFrom` all revert for such tokens as long as the owning Sei address remains unassociated. This is a genuine, reachable freezing-of-funds/assets condition for any protocol built on top of the ERC721 pointer (marketplaces, NFT-collateralized lending, auctions) analogous to the reported Debita issue, where legitimate parties cannot exercise ownership/claim rights due to an unhandled address-resolution edge case in a shared lookup helper.

### Likelihood Explanation
High likelihood: no privileged action or malicious validator/peer behavior is required. Any Sei-native holder who has not associated an EVM address (a common, non-adversarial default state for CosmWasm-only users, module accounts, or DAOs) and later becomes owner of a CW721 token that has an ERC721 pointer will trigger this whenever any EVM actor (even themselves, if they try to interact through the pointer) calls `ownerOf`, `tokenURI`, or `transferFrom`.

### Recommendation
Update `CW721ERC721Pointer.ownerOf()` (and any other pointer function relying on `AddrPrecompile.getEvmAddr`) to handle the "not associated" case gracefully instead of reverting — e.g., return a deterministic default/placeholder EVM address (analogous to `GetSeiAddressOrDefault`/`GetEVMAddressOrDefault` used elsewhere in the precompile layer) so that ownership/transfer logic for pointer-wrapped CW721 tokens does not permanently break when the underlying owner has no EVM association.

### Proof of Concept
1. Mint/transfer a CW721 token to a Sei-native address (or CosmWasm contract address) that has never called `associate` on the Addr precompile.
2. Deploy/obtain a `CW721ERC721Pointer` for that collection.
3. Call `pointer.ownerOf(tokenId)` (or `tokenURI(tokenId)`, or `transferFrom(...)`) from any EVM account.
4. The call reverts with `"sei address %s is not associated"` from [5](#0-4) 
propagated through [1](#0-0) 
permanently blocking any EVM-side interaction with that NFT until the owner independently associates an EVM address (which they may never do).

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L81-87)
```text
    function ownerOf(uint256 tokenId) public view override returns (address) {
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("owner_of", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes memory owner_ = JsonPrecompile.extractAsBytes(response, "owner");
        return AddrPrecompile.getEvmAddr(string(owner_));
    }
```

**File:** contracts/src/CW721ERC721Pointer.sol (L135-143)
```text
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        // revert if token isn't owned
        ownerOf(tokenId);
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("nft_info", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes memory uri = JsonPrecompile.extractAsBytes(response, "token_uri");
        return string(uri);
    }
```

**File:** contracts/src/CW721ERC721Pointer.sol (L160-169)
```text
    function transferFrom(address from, address to, uint256 tokenId) public override {
        if (to == address(0)) {
            revert ERC721InvalidReceiver(address(0));
        }
        require(from == ownerOf(tokenId), "`from` must be the owner");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("transfer_nft", _curlyBrace(_join(recipient, tId, ","))));
        _execute(bytes(req));
    }
```

**File:** precompiles/addr/addr.go (L137-158)
```go
func (p PrecompileExecutor) getEvmAddr(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	seiAddr, err := sdk.AccAddressFromBech32(args[0].(string))
	if err != nil {
		return nil, 0, err
	}

	evmAddr, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if !found {
		metrics.IncrementAssociationError("getEvmAddr", types.NewAssociationMissingErr(args[0].(string)))
		return nil, 0, fmt.Errorf("sei address %s is not associated", args[0].(string))
	}
	ret, err = method.Outputs.Pack(evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```
