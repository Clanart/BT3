### Title
Unauthorized ERC721/CW721 NFT Transfer via Missing Caller-Authorization Check in `CW721ERC721Pointer.transferFrom` - ([File: contracts/src/CW721ERC721Pointer.sol])

### Summary
The `CW721ERC721Pointer` contract, which acts as an EVM-facing pointer for a CosmWasm CW721 NFT contract, overrides `transferFrom(address from, address to, uint256 tokenId)` but only validates that `from` is the token's actual owner — it never checks that `msg.sender` is the owner, an approved spender, or an approved operator. Any address (any EVM transaction sender) can therefore call `transferFrom(victim, attacker, tokenId)` for a token they do not own and do not have approval for, and the pointer will execute a `transfer_nft` CosmWasm message on the underlying CW721 contract, moving the NFT away from its legitimate owner.

### Finding Description
`CW721ERC721Pointer.transferFrom` is defined as: [1](#0-0) 

```solidity
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

The only authorization-adjacent check present is `require(from == ownerOf(tokenId), ...)`, which verifies the *state* of the object being acted on (analogous to Bagisto's reorder controller which only validated that an order ID existed) but never verifies that the caller (`msg.sender`) is entitled to move that object. There is no `msg.sender == from`, no `getApproved(tokenId) == msg.sender`, and no `isApprovedForAll(from, msg.sender)` check — even though the contract itself implements `getApproved` and `isApprovedForAll` (used elsewhere for read access), it never consults them in `transferFrom`. `_execute` then delegatecalls the wasmd precompile to submit a `transfer_nft` execute message to the underlying CW721 contract on behalf of the pointer, using the caller-supplied `from`/`to`/`tokenId` values: [2](#0-1) 

This directly parallels the reported Bagisto IDOR pattern: the vulnerable function retrieves/acts on a resource using only a caller-supplied identifier (`from`/`tokenId`) without verifying the caller is authorized to operate on it, unlike sibling functions in the same file (`approve`, `setApprovalForAll`) that at least tie the action to `msg.sender`'s intent, and unlike the CW1155 pointer's `safeTransferFrom`, which correctly enforces `require(msg.sender == from || isApprovedForAll(from, msg.sender), "ERC1155: caller is not approved to transfer")`: [3](#0-2) 

For comparison, the ERC20 pointer's `transferFrom` correctly reduces the spender's allowance before submitting the CosmWasm `transfer_from` message, which implicitly enforces authorization via the CW20 contract's own allowance check: [4](#0-3)  — however, the CW721 pointer's `transferFrom` skips any analogous authorization step and submits a bare `transfer_nft` message that the underlying CW721 contract will execute as coming from the pointer contract's own Sei address (the pointer is likely the on-chain "owner" of record from the CosmWasm module's perspective, or otherwise trusted), meaning the CW721 module itself may not re-validate the true EVM caller's rights.

### Impact Explanation
This allows any unprivileged EVM transaction sender who can call the `CW721ERC721Pointer` contract for a given CW721 collection to move (steal) any NFT out of another user's wallet to an address of the attacker's choosing, without needing that NFT owner's approval or private key. This is a direct unauthorized-transfer / fund-loss impact (loss of NFT assets), which is more severe than the referenced Bagisto disclosure-only IDOR (which merely exposed order line items) since it results in permanent, unauthorized asset transfer.

### Likelihood Explanation
High likelihood if confirmed: `transferFrom` is a public, standard ERC721 function that any EVM account or contract can call directly against a deployed pointer address once the CW721↔ERC721 pointer exists (pointers are createable by any user via the pointer precompile for CW721 contracts). No special privilege, association, or approval is required to trigger the exploit — the only precondition is knowing/enumerating a `tokenId` and its current owner's EVM address, both of which are readable on-chain via `ownerOf`.

### Recommendation
Add an explicit caller-authorization check in `transferFrom` (and ensure any inherited/overridden `safeTransferFrom` paths route through the same check), mirroring the ERC721 standard and the pattern already used in `CW1155ERC1155Pointer.safeTransferFrom`:
```solidity
require(
    msg.sender == from || getApproved(tokenId) == msg.sender || isApprovedForAll(from, msg.sender),
    "ERC721: caller is not owner nor approved"
);
```
This should be placed before constructing and executing the `transfer_nft` CosmWasm message.

### Proof of Concept
1. Victim owns `tokenId=1` on a CW721 collection with an associated `CW721ERC721Pointer` at address `P`.
2. Attacker (any EVM account, no approval) calls:
   ```
   P.transferFrom(victimEvmAddress, attackerEvmAddress, 1)
   ```
3. The pointer only checks `from == ownerOf(tokenId)`, which is true, and does not check `msg.sender`.
4. The pointer submits `transfer_nft` to the underlying CW721 contract; the NFT is moved to the attacker's Sei address.
5. `P.ownerOf(1)` now returns `attackerEvmAddress`, confirming unauthorized transfer.

Note: I was not able to fully trace how the underlying wasmd/CW721 module authenticates the `Sender` field for the `transfer_nft` message issued via `_execute`'s delegatecall to the wasmd precompile (i.e., whether the CW721 contract treats the pointer contract's own Sei address as the effective owner/sender for authorization purposes). Confirming the end-to-end exploitability requires verifying that the wasmd precompile's `execute` path (invoked via `delegatecall`) uses the pointer contract's own associated Sei address as `Sender` when submitting `transfer_nft`, which would mean the CW721 module has no visibility into the true EVM `msg.sender` and relies entirely on the pointer's own internal checks — the missing check identified above.

### Citations

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

**File:** contracts/src/CW721ERC721Pointer.sol (L187-198)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw721Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L78-89)
```text
    function safeBatchTransferFrom(
        address from,
        address to,
        uint256[] memory ids,
        uint256[] memory amounts,
        bytes memory data
    ) public override {
        require(to != address(0), "ERC1155: transfer to the zero address");
        require(
            msg.sender == from || isApprovedForAll(from, msg.sender),
            "ERC1155: caller is not approved to transfer"
        );
```

**File:** contracts/src/CW20ERC20Pointer.sol (L91-96)
```text
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```
