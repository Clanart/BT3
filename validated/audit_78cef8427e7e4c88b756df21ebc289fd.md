Found a concrete match. The `CW721ERC721Pointer` contract's `transferFrom()` completely omits any authorization check — the equivalent of the reported `transferFrom` allowance bypass, but for NFTs pointer-mapped from CosmWasm CW721 contracts.

### Title
Unauthorized NFT transfer via `CW721ERC721Pointer.transferFrom()` missing approval/operator check - ([File: contracts/src/CW721ERC721Pointer.sol])

### Summary
`CW721ERC721Pointer.transferFrom()` only verifies that `from` is the current owner of the token, but never checks that `msg.sender` is the owner, an approved address, or an approved operator. Any account can therefore move any pointer-tracked NFT out of any owner's wallet by simply calling `transferFrom(owner, attacker, tokenId)`.

### Finding Description
`CW721ERC721Pointer` is a Solidity ERC721 pointer over a CosmWasm CW721 contract. Its `transferFrom` override is: [1](#0-0) 

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

Compare this to the codebase's own reference `ERC721.sol` implementation which correctly gates the transfer behind `_isApprovedOrOwner(from, msg.sender, id)`: [2](#0-1) 

The pointer's `transferFrom` never checks `msg.sender == from`, nor `isApprovedForAll(from, msg.sender)`, nor `getApproved(tokenId) == msg.sender`. It only requires `from` to be the token's current owner — a fact anyone can trivially satisfy just by naming the real owner as `from`. The underlying `_execute` call then dispatches a `transfer_nft` CW721 message signed and executed as the pointer contract itself (via `delegatecall` to the wasmd precompile), so the CW721 contract sees the transfer as being authorized by the pointer contract's own Sei address rather than by `msg.sender`, and the CW721 side has no way to reject it as unauthorized either: [3](#0-2) 

Because `approve()` in the pointer is similarly unguarded (no check that `msg.sender` is the current owner or an existing approved operator) but that is not even needed to exploit this — `transferFrom` bypasses the authorization step entirely.

### Impact Explanation
Any unprivileged EVM transaction sender can steal any NFT that has a CW721 EVM pointer contract deployed, by calling `transferFrom(victimOwner, attackerAddress, tokenId)` directly, with no approval, no signature from the owner, and no CW721-side authorization check performed by the pointer. This is a direct, permanent loss of NFT ownership/funds for any user holding assets through a CW721 pointer — matching the "unauthorized transfer via precompile or pointer" and "concrete fund loss" acceptance criteria.

### Likelihood Explanation
Trivial and always reachable: it requires only a single public EVM transaction calling `transferFrom` on a `CW721ERC721Pointer` instance with a known `tokenId` and current owner address (both of which are queryable on-chain via `ownerOf`). No special privileges, front-running, or race conditions needed.

### Recommendation
Add an authorization check in `transferFrom` (and enforce owner/operator checks in `approve`), mirroring OpenZeppelin's ERC721 semantics and the project's own `ERC721.sol` reference implementation:
```solidity
require(
    msg.sender == from || isApprovedForAll(from, msg.sender) || getApproved(tokenId) == msg.sender,
    "ERC721: caller is not token owner or approved"
);
```
before dispatching the `transfer_nft` CW message.

### Proof of Concept
1. Deploy/locate a `CW721ERC721Pointer` for an existing CW721 collection where `victim` owns `tokenId`.
2. From any unrelated EVM account `attacker` (no approval granted), call:
   `pointer.transferFrom(victim, attacker, tokenId)`.
3. The `require(from == ownerOf(tokenId), ...)` check passes (since `victim` is indeed the owner), the authorization check that should stop `attacker` is absent, and `_execute` dispatches `transfer_nft` to the underlying CW721 contract, transferring the NFT to `attacker`.

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

**File:** contracts/src/ERC721.sol (L121-134)
```text
    function transferFrom(address from, address to, uint id) public {
        require(from == _ownerOf[id], "from != owner");
        require(to != address(0), "transfer to zero address");

        require(_isApprovedOrOwner(from, msg.sender, id), "not authorized");

        _balanceOf[from]--;
        _balanceOf[to]++;
        _ownerOf[id] = to;

        delete _approvals[id];

        emit Transfer(from, to, id);
    }
```
