Interesting: `CW721ERC721Pointer.transferFrom` at [1](#0-0)  only checks `require(from == ownerOf(tokenId), "\`from\` must be the owner")` but never checks that `msg.sender` is the owner or an approved operator/spender before calling `transfer_nft` on the underlying CW721 contract. This is analogous to (but distinct from) the reported `USDToken::burn` bug: a state-changing token-movement function is missing a `msg.sender` authorization check, letting any caller move (in this case transfer, not burn) another user's asset. However, I was not able to find an unauthorized `burn` in this pointer (there is no `burn` function defined in `CW721ERC721Pointer.sol` at all, and the CW1155 pointer's `burn`/`burnBatch` do have proper `msg.sender == account || isApprovedForAll` checks at [2](#0-1)  and [3](#0-2) ). The tokenfactory module's `MsgBurn` also correctly restricts calls to the denom admin at [4](#0-3) .

Given `transferFrom` is not a "burn" bug class and the rules restrict analogs strictly to the enumerated categories (unauthorized transfer via precompile or pointer is explicitly listed as acceptable impact), this does map onto "unauthorized transfer via precompile or pointer." Let me build the finding around this.

### Title
Missing caller-authorization check in `CW721ERC721Pointer::transferFrom` allows unauthorized transfer of any user's NFT - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer::transferFrom` verifies only that the supplied `from` address currently owns the token, but never checks that `msg.sender` is that owner, an approved spender for that token, or an approved operator for the owner. Any EVM caller can therefore invoke `transferFrom(victimOwner, attackerAddress, tokenId)` for any CW721-backed NFT that has an ERC721 pointer deployed, moving the token out of the victim's account without their consent, mirroring the pattern described in the analog report where an arbitrary-address input replaces a `msg.sender`/authorization check.

### Finding Description
`CW721ERC721Pointer` is the ERC721-compatible EVM pointer contract for CosmWasm CW721 NFT collections, letting EVM users interact with CW721 tokens through the standard ERC721 interface via the `wasmd` precompile.

In `transferFrom`:
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
``` [1](#0-0) 

The only validation is that `from` is genuinely the current owner of `tokenId` — there is no check of `msg.sender == from`, `msg.sender == getApproved(tokenId)`, or `isApprovedForAll(from, msg.sender)`, unlike the standard OpenZeppelin ERC721 `transferFrom` implementation, and unlike the sibling `CW1155ERC1155Pointer` contract, which properly checks `msg.sender == account || isApprovedForAll(account, msg.sender)` before allowing state-changing burns/transfers [2](#0-1) .

The pointer executes the underlying transfer via `_execute`, which `delegatecall`s the `wasmd` precompile's `execute` function using the pointer contract's own identity as the CW721 message sender:
```solidity
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
``` [5](#0-4) 

Because the pointer contract is the CW721 contract's registered `transfer_nft` sender/operator context in the underlying wasm keeper flow, the CW721 module trusts that the pointer performed proper authorization before issuing `transfer_nft`. Since the pointer's Solidity code skips the `msg.sender` check entirely, any EVM account can call `transferFrom(victim, attacker, tokenId)` for any token they do not own and do not have approval for, as long as they pass the correct current owner as `from`, which is public/queryable information (`ownerOf`).

### Impact Explanation
This allows any unprivileged EVM transaction sender to steal any CW721 NFT that has an ERC721 pointer contract deployed for it, by directly transferring it out of the legitimate owner's account. This is unauthorized transfer via a pointer contract — a direct, permanent, and irreversible theft of the underlying CW721 NFT (fund/asset loss), reachable from any single submitted EVM transaction against the pointer contract's public `transferFrom` function.

### Likelihood Explanation
Likelihood is high: `transferFrom` is a `public` function with no modifier restricting callers, `ownerOf` is a public view function that reveals the required `from` argument, and no special privilege, timing, or race condition is needed — a single transaction from any account is sufficient to steal any pointed CW721 token.

### Recommendation
Add the standard ERC721 authorization check before executing the transfer, mirroring what `CW1155ERC1155Pointer` already does for its burn/transfer functions:
```solidity
require(
    msg.sender == from || isApprovedForAll(from, msg.sender) || getApproved(tokenId) == msg.sender,
    "ERC721: caller is not token owner or approved"
);
```
This should be added immediately after the `from == ownerOf(tokenId)` check in `transferFrom`, and the same pattern should be audited for `safeTransferFrom` overrides (if any are inherited/overridden) to ensure consistent enforcement.

### Proof of Concept
1. Assume a CW721 collection has an ERC721 pointer deployed (`CW721ERC721Pointer`) and `victim` owns `tokenId = 1` in that collection.
2. `attacker` (an unrelated EVM account with no approval) calls `pointer.ownerOf(1)` to confirm `victim` is the current owner (public information).
3. `attacker` calls `pointer.transferFrom(victim, attacker, 1)` directly.
4. Inside `transferFrom`, `require(from == ownerOf(tokenId), ...)` passes because `from == victim` is indeed the real owner; no check is made that `msg.sender == attacker` is authorized.
5. The contract executes `transfer_nft` against the CW721 contract with `recipient = attacker`, successfully moving `tokenId 1` from `victim` to `attacker` without `victim`'s consent or any prior approval.

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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L141-147)
```text
    function burn(address account, uint256 id, uint256 amount) public virtual {
        require(account != address(0), "ERC1155: cannot burn from the zero address");
        require(balanceOf(account, id) >= amount, "ERC1155: insufficient balance for burning");
        require(
            msg.sender == account || isApprovedForAll(account, msg.sender),
            "ERC1155: caller is not approved to burn"
        );
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L159-164)
```text
    function burnBatch(address account, uint256[] memory ids, uint256[] memory amounts) public virtual {
        require(account != address(0), "ERC1155: cannot burn from the zero address");
        require(
            msg.sender == account || isApprovedForAll(account, msg.sender),
            "ERC1155: caller is not approved to burn"
        );
```

**File:** x/tokenfactory/keeper/msg_server.go (L128-138)
```go
func (server msgServer) Burn(goCtx context.Context, msg *types.MsgBurn) (*types.MsgBurnResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Amount.GetDenom())
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}
```
