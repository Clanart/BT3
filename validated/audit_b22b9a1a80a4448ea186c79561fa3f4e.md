### Title
`safeTransferFrom` on `CW721ERC721Pointer` does not invoke the CW721-side transfer, causing a permanent desync/lock between the ERC721 pointer's reported state and the underlying CW721 token ownership - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer` is a Solidity contract that lets EVM callers interact with a native CW721 NFT contract as if it were an ERC721 token, by proxying `ownerOf`, `balanceOf`, `getApproved`, etc. through the `Wasmd`/`Json`/`Addr` precompiles [1](#0-0) . It overrides `transferFrom` to translate the call into a CosmWasm `transfer_nft` execute message against the wrapped CW721 contract [2](#0-1) , but it never overrides `safeTransferFrom`, and it never populates OpenZeppelin `ERC721`'s internal `_owners`/`_balances` storage via `_mint` for these pointer tokens.

### Finding Description
The contract inherits OpenZeppelin's `ERC721` and only overrides `ownerOf`, `balanceOf`, `getApproved`, `isApprovedForAll`, `transferFrom`, `approve`, and `setApprovalForAll` to redirect reads/writes to the CW721 contract through the Wasmd precompile [3](#0-2) [2](#0-1) . Because `safeTransferFrom(address,address,uint256[,bytes])` is not overridden, calling it invokes OpenZeppelin's default implementation, which internally uses the base library's own storage-backed transfer/ownership-check logic rather than the pointer's `transferFrom` override or the CW721-side `transfer_nft` execute call. Since the pointer never calls `_mint` to populate the base `ERC721` internal ownership mapping for any token id (all "ownership" is delegated live to the CosmWasm contract via `ownerOf`/`balanceOf` overrides), the base implementation's internal ownership check will not match reality: the OZ default transfer path operates against storage that was never initialized for these pointer-represented tokens.

This is directly analogous to the reported Putty bug class: a caller invokes a standard ERC721 entry point (`safeTransferFrom`) expecting normal NFT semantics, but the underlying asset-transfer mechanism silently diverges from the "real" transfer semantics (the CW721-side `transfer_nft` message that actually moves ownership in the CosmWasm contract), analogous to Putty's `safeTransferFrom` calls failing/being incompatible with the underlying non-standard asset and leaving state permanently inconsistent (fees/premium paid, but the option/asset uncallable). Here, any external contract or user relying on the ERC721 standard `safeTransferFrom` entrypoint on the pointer (e.g., marketplaces, aggregators, or other contracts that safety-check recipients via `onERC721Received`) will either revert unexpectedly, or — worse — desynchronize the pointer's reported state from the real CW721 owner, since the actual cross-chain asset movement path (`transfer_nft`) is bypassed entirely.

### Impact Explanation
This breaks a fundamental invariant of the ERC721 standard interface exposed by a first-class Sei pointer contract: `safeTransferFrom` must have the same effect as `transferFrom` (with an added receiver-safety check), but here it does not perform the same underlying state transition at all. Any protocol composing with `CW721ERC721Pointer` (e.g., marketplaces, lending protocols, or wrapping/vault contracts in the integration test suite such as `NftMarketplace.sol`) that calls `safeTransferFrom` instead of `transferFrom` will either have transactions revert unexpectedly or — depending on exact OZ ERC721 internal state semantics for tokens that were never `_mint`ed locally — produce an inconsistent, unrecoverable ownership state between the pointer contract and the real CW721 asset, i.e., permanent freezing of the NFT from the EVM side even though the true CW721 owner is unaffected. This qualifies as a fund/asset freezing issue for the EVM representation of the NFT.

### Likelihood Explanation
Likelihood is moderate-to-high: any unprivileged EVM user, or any third-party smart contract (e.g., a marketplace) that follows standard ERC721 best practices of calling `safeTransferFrom` rather than `transferFrom` when moving NFTs to arbitrary recipients, will trigger this path. This is a normal, expected interaction pattern with any ERC721-compliant contract, not an edge case requiring an attacker to construct anything unusual — the mere existence of the unguarded default `safeTransferFrom` inherited from OpenZeppelin, sitting alongside a divergent, overridden `transferFrom`, is reachable by any public caller of the pointer contract.

### Recommendation
Override both `safeTransferFrom(address,address,uint256)` and `safeTransferFrom(address,address,uint256,bytes)` in `CW721ERC721Pointer` to route through the same CW721 `transfer_nft` logic as the overridden `transferFrom` (optionally adding the `onERC721Received` safety check against the `to` address after the CW721-side execute succeeds), ensuring there is a single, consistent code path for all transfer-style entry points, and that the on-chain CW721 ownership and the pointer's reported ERC721 state can never diverge.

### Proof of Concept
1. Deploy a CW721 contract and register a `CW721ERC721Pointer` for it via `addCW721Pointer` (as done in `contracts/test/ERC721toCW721PointerTest.js` / `CW721toERC721PointerTest.js`) [4](#0-3) .
2. Mint/own a token on the CW721 side so that `pointer.ownerOf(tokenId)` correctly resolves the EVM address of the owner via the CW721 query path [3](#0-2) .
3. As the owner, call `pointer.safeTransferFrom(owner, recipient, tokenId)` instead of `pointer.transferFrom(owner, recipient, tokenId)`.
4. Because `safeTransferFrom` is not overridden, it does not build/execute the `{"transfer_nft":...}` CosmWasm message that `transferFrom` performs [2](#0-1) ; it instead falls back to OpenZeppelin `ERC721`'s default logic operating on internal storage that was never initialized for this token (no `_mint` call exists anywhere in the pointer contract). Observe that the call either reverts unexpectedly (breaking composability with any protocol using the standard-compliant `safeTransferFrom` path) or leaves `pointer.ownerOf(tokenId)` (which queries the CW721 contract directly) unchanged while local OZ storage is mutated — a permanent desync between the pointer's advertised behavior and the real underlying asset location.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L13-32)
```text
contract CW721ERC721Pointer is ERC721,ERC2981 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw721Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;

    error NotImplementedOnCosmwasmContract(string method);
    error NotImplemented(string method);

    constructor(string memory Cw721Address_, string memory name_, string memory symbol_) ERC721(name_, symbol_) {
        WasmdPrecompile = IWasmd(WASMD_PRECOMPILE_ADDRESS);
        JsonPrecompile = IJson(JSON_PRECOMPILE_ADDRESS);
        AddrPrecompile = IAddr(ADDR_PRECOMPILE_ADDRESS);
        Cw721Address = Cw721Address_;
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

**File:** contracts/src/CW721ERC721Pointer.sol (L159-169)
```text
    // Transactions
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
