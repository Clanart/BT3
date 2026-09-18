### Title
`CW721ERC721Pointer` overrides only `transferFrom()` and leaves the inherited OpenZeppelin `safeTransferFrom()` unwired to the CW721 bridge, causing tokens sent to non-receiving contracts to become unrecoverable - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer` is an EVM ERC721 wrapper that proxies all NFT state and transactions to an underlying CosmWasm CW721 contract via the wasmd precompile. It overrides `ownerOf`, `getApproved`, `isApprovedForAll`, `approve`, `setApprovalForAll` and `transferFrom` to route through the precompile bridge, but it does **not** override `safeTransferFrom(address,address,uint256)` / `safeTransferFrom(address,address,uint256,bytes)`, nor the internal OpenZeppelin hooks they rely on (`_ownerOf`, `_isAuthorized`/`_isApprovedOrOwner`, `_update`/`_transfer`).

### Finding Description
The overridden `transferFrom` implementation bypasses OpenZeppelin's internal accounting entirely and talks directly to the wasm CW721 contract: [1](#0-0) 

However, `safeTransferFrom` is never overridden in this contract, so calls resolve to the base OpenZeppelin `ERC721.safeTransferFrom`, which does not call the overridden public `transferFrom`, but instead calls the internal `_safeTransfer` → `_update`/`_transfer` path that reads/writes OpenZeppelin's own `_owners`/`_tokenApprovals`/`_operatorApprovals` storage. Since this pointer contract never mints tokens into that internal storage (all ownership data lives exclusively in the CW721 wasm contract, queried on demand via `ownerOf`, `getApproved`, `isApprovedForAll`), the base-contract's internal `_ownerOf`/ownership checks for any `tokenId` are always the zero address, meaning `safeTransferFrom` will always revert with `ERC721NonexistentToken`/`ERC721InsufficientApproval`.

This can be contrasted with the sibling `CW1155ERC1155Pointer`, which correctly overrides `safeTransferFrom` and `safeBatchTransferFrom` to route through `_execute` and manually invoke `onERC1155Received`/`onERC1155BatchReceived`: [2](#0-1) 

By contrast, `CW721ERC721Pointer` has no equivalent working `safeTransferFrom` override, and no `onERC721Received` check is ever performed in the entire contract: [3](#0-2) 

The net effect: the only functional transfer path on this pointer is the unsafe `transferFrom`, which — exactly as described in the referenced report — performs no check that the recipient (`to`) is capable of handling ERC721 tokens. There is no working `safeTransferFrom` fallback to protect users, since that function is permanently broken/reverting due to the unsynced OpenZeppelin storage.

### Impact Explanation
Any unprivileged EVM user (or integrating dapp/marketplace) transferring a CW721-pointer-wrapped NFT into a smart contract that does not implement `onERC721Received` (e.g., a naive vault, staking pool, or bridge contract) via `transferFrom` will have that token become **permanently stuck**, with no built-in safety mechanism to prevent it, because the "safe" transfer variant that should have blocked such transfers is completely inoperative on this contract. This is a permanent-freezing-of-funds class issue reachable by any pointer-holder or any contract composing with `IERC721`.

### Likelihood Explanation
High likelihood of triggering: any wallet, dapp, or contract that calls the standard `IERC721.safeTransferFrom` (a very common integration pattern, and the function explicitly recommended by EIP-721 for transfers to contracts) will unconditionally revert on this pointer, and any caller who instead uses `transferFrom` (which succeeds) is exposed to loss with zero receiver validation. No special privilege is required — an ordinary transaction sender who owns/controls a pointer-wrapped CW721 NFT can trigger the issue.

### Recommendation
Override `safeTransferFrom(address,address,uint256)` and `safeTransferFrom(address,address,uint256,bytes)` in `CW721ERC721Pointer`, mirroring the pattern already used in `CW1155ERC1155Pointer`: perform the same wasm `_execute` transfer as `transferFrom`, and then check `to.code.length > 0` and require `IERC721Receiver(to).onERC721Received(...)` returns the correct selector, reverting otherwise.

### Proof of Concept
1. Deploy/obtain a `CW721ERC721Pointer` for an existing CW721 collection and own token `tokenId`.
2. Deploy a plain contract `Sink` with no `onERC721Received` implementation.
3. Call `pointer.transferFrom(owner, address(Sink), tokenId)` — succeeds per the override at [4](#0-3) , and the token becomes permanently controlled by `Sink`, which cannot move it.
4. Alternatively call `pointer.safeTransferFrom(owner, address(Sink), tokenId)` expecting it to protect against step 3 — it instead reverts (`ERC721NonexistentToken`) because it operates on the inherited OpenZeppelin storage which the pointer never populates, confirming there is no working safety mechanism to prevent step 3's outcome.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L1-40)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.12;

import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "@openzeppelin/contracts/utils/Strings.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";

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

    function supportsInterface(bytes4 interfaceId) public pure override(ERC721, ERC2981) returns (bool) {
        return
            interfaceId == type(IERC2981).interfaceId ||
            interfaceId == type(IERC165).interfaceId ||
            interfaceId == type(IERC721).interfaceId ||
            interfaceId == type(IERC721Metadata).interfaceId;
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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L41-76)
```text
    function safeTransferFrom(
        address from,
        address to,
        uint256 id,
        uint256 amount,
        bytes memory data
    ) public override {
        require(to != address(0), "ERC1155: transfer to the zero address");
        require(balanceOf(from, id) >= amount, "ERC1155: insufficient balance for transfer");
        require(
            msg.sender == from || isApprovedForAll(from, msg.sender),
            "ERC1155: caller is not approved to transfer"
        );
    
        string memory f = _formatPayload("from", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory t = _formatPayload("to", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(id)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));

        string memory req = _curlyBrace(
            _formatPayload("send", _curlyBrace(_join(f, ",", _join(t, ",", _join(tId, ",", amt)))))
        );
        _execute(bytes(req));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155Received(
                    msg.sender,
                    from,
                    id,
                    amount,
                    data
                ) == IERC1155Receiver.onERC1155Received.selector,
                "unsafe transfer"
            );
        }
    }
```
