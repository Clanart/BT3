### Title
`CW721ERC721Pointer`'s inherited `safeTransferFrom` always reverts, permanently freezing standard NFT transfers for CW721 pointer tokens - (File: `contracts/src/CW721ERC721Pointer.sol`)

### Summary
`CW721ERC721Pointer` is the canonical EVM-side ERC721 representation of a native CW721 collection, created by the pointer precompile (`AddCW721`) and used by any EVM contract or wallet that wants to interact with a Sei CW721 NFT collection through the standard ERC721 interface. The contract overrides `ownerOf`, `balanceOf`, `getApproved`, `isApprovedForAll`, `approve`, `setApprovalForAll` and `transferFrom` to proxy all state to the CosmWasm contract via the `wasmd`/`json`/`addr` precompiles, but it never populates the OpenZeppelin `ERC721` base contract's internal `_owners` mapping (no `_mint`/`_safeMint`/`_update` call ever happens, since ownership always lives in the CW721 module).

### Finding Description
`CW721ERC721Pointer` inherits from OpenZeppelin's `ERC721`, and only overrides `transferFrom`, not `safeTransferFrom`: [1](#0-0) 

Because `safeTransferFrom(address,address,uint256[,bytes])` is left as the inherited OZ implementation, it uses OZ's internal `_isApprovedOrOwner`, which explicitly qualifies the base-class call as `ERC721.ownerOf(tokenId)` (bypassing virtual dispatch) to read the *internal* `_owners` mapping rather than the pointer's overridden `ownerOf()` (which queries the real CW721 owner through the wasmd precompile): [2](#0-1) 

Since `_owners[tokenId]` is never written by this contract (there is no code path in `CW721ERC721Pointer` that calls `_mint`/`_update`), `ERC721.ownerOf(tokenId)` always resolves to `address(0)` for every CW721-backed token, and OZ's `ownerOf` reverts with `ERC721NonexistentToken`/`ERC721: invalid token ID` before the approval/ownership check can even run. This means **`safeTransferFrom` will unconditionally revert for every token id on every `CW721ERC721Pointer` instance**, regardless of who calls it or who the real owner is, while the custom `transferFrom` override works fine because it independently re-derives ownership via `ownerOf(tokenId)` (the pointer's overridden version) and executes `transfer_nft` on the underlying CW721 contract: [3](#0-2) 

### Impact Explanation
`safeTransferFrom` is the ERC721-standard, security-recommended transfer method (it protects against sending NFTs to non-receiver contracts) and is the default method used by most EVM tooling, wallets, and marketplace/dApp contracts (e.g. OpenZeppelin-based marketplaces, `ERC721Holder`-based vault contracts, and any integrator following best practice). Because the CW721→ERC721 pointer is the canonical bridge contract exposed to EVM consumers of native Sei NFTs (used by the NFT marketplace dapp test flow and any third-party integrator), any contract or wallet that calls `safeTransferFrom` on a CW721 pointer will permanently and unconditionally fail. This is a broken, unfixable-without-redeploy core function reachable by any public RPC/contract caller, matching the "permanent freezing" impact class: the safe-transfer pathway for CW721-backed EVM assets is bricked for the lifetime of the pointer contract.

### Likelihood Explanation
100% likelihood/deterministic: any account calling `safeTransferFrom(from, to, tokenId)` or `safeTransferFrom(from, to, tokenId, data)` on any deployed `CW721ERC721Pointer` will trigger the revert, with no special preconditions other than the token existing in the underlying CW721 contract. No malicious actor or privileged role is needed — a single ordinary EVM transaction from an unprivileged user triggers it.

### Recommendation
Override both `safeTransferFrom(address,address,uint256)` and `safeTransferFrom(address,address,uint256,bytes)` in `CW721ERC721Pointer` to mirror the same logic as the existing `transferFrom` override (verify `from == ownerOf(tokenId)` via the pointer's proxied `ownerOf`, execute the `transfer_nft` CosmWasm message, and optionally perform an ERC721Receiver check on `to` if it is a contract) instead of relying on OpenZeppelin's default implementation, which depends on the unused internal `_owners` mapping.

### Proof of Concept
1. Deploy/obtain a `CW721ERC721Pointer` for an existing CW721 collection (e.g. via `deployErc721PointerForCw721` in `contracts/test/lib.js`, or through the pointer precompile's `AddCW721`).
2. Mint/own a token in the underlying CW721 contract, confirmed via `pointer.ownerOf(tokenId)` returning the correct EVM-mapped owner (this works, since it uses the overridden query).
3. As the legitimate owner, call `pointer.safeTransferFrom(owner, recipient, tokenId)`.
4. The call reverts with `ERC721NonexistentToken`/`ERC721: invalid token ID`, because OZ's inherited `_isApprovedOrOwner` calls the qualified base `ERC721.ownerOf(tokenId)`, which reads the never-populated `_owners` mapping — this occurs for every token, every caller, regardless of real CW721 ownership, while the equivalent `transferFrom(owner, recipient, tokenId)` call succeeds.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L34-40)
```text
    function supportsInterface(bytes4 interfaceId) public pure override(ERC721, ERC2981) returns (bool) {
        return
            interfaceId == type(IERC2981).interfaceId ||
            interfaceId == type(IERC165).interfaceId ||
            interfaceId == type(IERC721).interfaceId ||
            interfaceId == type(IERC721Metadata).interfaceId;
    }
```

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
