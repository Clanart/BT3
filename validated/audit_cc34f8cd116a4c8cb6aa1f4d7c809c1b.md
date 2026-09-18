### Title
Untrusted external call to `onERC1155Received`/`onERC721Received` after state-mutating CW-side transfer enables ERC777-style cross-contract reentrancy in CW1155/CW721 pointer contracts - (File: contracts/src/CW1155ERC1155Pointer.sol, contracts/src/CW721ERC721Pointer.sol)

### Summary
The `CW1155ERC1155Pointer.safeTransferFrom`/`safeBatchTransferFrom` functions, and `CW721ERC721Pointer` (via the inherited OpenZeppelin `safeTransferFrom`), execute the actual token-ledger-mutating call into the underlying CosmWasm contract (`_execute`) and only afterwards call an attacker-controlled `to` contract's `onERC1155Received`/`onERC721Received` hook, with no reentrancy guard anywhere in the pointer contracts. This is structurally identical to the ERC777 `tokensReceived`/`tokensToSend` hook problem described in the report: an unprivileged token recipient gets arbitrary control flow *after* the authoritative state (CW1155/CW721 ownership on the Cosmos side) has already changed but *before* the top-level EVM call/transaction that initiated the transfer has finished, allowing reentrant calls into the pointer itself or into any other contract/module that composes with it while its own accounting is mid-flight.

### Finding Description
In `CW1155ERC1155Pointer.safeTransferFrom`, the wasmd `send` message is executed via `_execute(bytes(req))`, which mutates the underlying CW1155 balances on the Cosmos side, and only after that does the contract invoke the external, caller-supplied `to` address: [1](#0-0) 

The same pattern repeats in `safeBatchTransferFrom`: [2](#0-1) 

`CW721ERC721Pointer` overrides `transferFrom` to perform the CW-side `transfer_nft` execute call, but does not override `safeTransferFrom`, so the inherited OpenZeppelin `ERC721.safeTransferFrom` still calls the overridden `transferFrom` (mutating CW-side ownership) and then invokes `IERC721Receiver(to).onERC721Received(...)` on the caller-controlled recipient: [3](#0-2) 

Neither pointer contract imports or applies `ReentrancyGuard`/`nonReentrant` anywhere in the transfer path: [4](#0-3) [5](#0-4) 

This mirrors exactly the attack-vector class in the external report: the pointer hands control flow to an unprivileged, attacker-chosen contract *after* the authoritative ledger state has already moved, but before the calling context (which may be another DeFi module, vault, or marketplace built atop the pointer) has finished its own bookkeeping for that same top-level call. Any protocol built on top of these pointer contracts that assumes the `safeTransferFrom`/`safeBatchTransferFrom` call is atomic and side-effect-free with respect to reentrancy is vulnerable to the same class of exploit described in the report (state read/write ordering exploited via a hostile receiver hook to desynchronize an external protocol's internal accounting from the actual pointer/CW balances).

### Impact Explanation
Because the CW-side balance mutation is finalized via `_execute` before the untrusted hook fires, and the hook can freely call back into: (a) the same pointer contract's other public/view functions (which now reflect the post-transfer state), (b) other Sei precompiles (bank, wasmd, addr), or (c) any external protocol contract that is mid-execution as the caller of `safeTransferFrom`, an attacker-controlled receiving contract can perform unauthorized nested transfers or manipulate a caller protocol's invariants (e.g., collateral/position accounting, escrow release, or LP share calculations) before the outer call completes — falling squarely under "unauthorized transfer via precompile or pointer." This can result in double-crediting or draining of any downstream protocol built on the CW1155/CW721 pointer's transfer functions.

### Likelihood Explanation
Likelihood is moderate: exploitation does not require any privileged access — any unprivileged EVM contract deployer can implement a hostile `onERC1155Received`/`onERC721Received` and call `safeTransferFrom`/`safeBatchTransferFrom` on a pointer contract with itself as the recipient. The pointer contracts themselves have no obvious internal double-spend (since `balanceOf` queries live CW state), so the practical impact depends on downstream composability — the vulnerability's severity scales with how many protocols on Sei rely on these pointer contracts' transfer functions without their own reentrancy protections, exactly as flagged in the original report ("any combination of modules may lead to a possible exploit").

### Recommendation
- Add `nonReentrant` (OpenZeppelin `ReentrancyGuard`) to `safeTransferFrom`, `safeBatchTransferFrom` in `CW1155ERC1155Pointer.sol`, and override `safeTransferFrom`/`safeTransferFrom(...,bytes)` in `CW721ERC721Pointer.sol` with the same guard.
- Alternatively/additionally, perform the external receiver-hook call using a checks-effects-interactions-safe pattern, and document/require that any protocol integrating these pointer contracts must independently guard against reentrancy from `to.code.length > 0` recipients.
- Consider adding a protocol-wide, cross-contract reentrancy lock at the EVM keeper level for pointer-triggered CW state mutations, consistent with the report's recommendation for protocol-wide cross-contract reentrancy prevention.

### Proof of Concept
1. Attacker deploys `MaliciousReceiver` implementing `onERC1155Received` that, upon being called, re-enters `CW1155ERC1155Pointer.safeTransferFrom` (or calls into another Sei precompile/protocol contract) using the balance that has already been credited to it by the just-completed `_execute` call.
2. Attacker calls `pointer.safeTransferFrom(attacker, address(MaliciousReceiver), id, amount, data)`.
3. `_execute` mutates the underlying CW1155 ledger, crediting `MaliciousReceiver` with `amount` of `id`.
4. Because `to.code.length > 0`, the pointer calls `MaliciousReceiver.onERC1155Received(...)` per [6](#0-5) .
5. Inside this callback, `MaliciousReceiver` reenters the pointer (or any other contract that is currently mid-execution as an upstream caller of `safeTransferFrom`) while that caller's own accounting logic has not yet observed/finalized the transfer, allowing it to trigger unauthorized additional transfers or manipulate the caller's invariants before the original `safeTransferFrom` call returns.

### Citations

**File:** contracts/src/CW1155ERC1155Pointer.sol (L1-30)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.12;

import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import "@openzeppelin/contracts/token/ERC1155/extensions/IERC1155MetadataURI.sol";
import "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";
import "@openzeppelin/contracts/utils/Strings.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";

contract CW1155ERC1155Pointer is ERC1155, ERC2981 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw1155Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;
    string public name;
    string public symbol;

    error NotImplementedOnCosmwasmContract(string method);
    error NotImplemented(string method);

```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L60-76)
```text
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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L115-121)
```text
        payload = string.concat(payload, "]}}");
        _execute(bytes(payload));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155BatchReceived(
                    msg.sender,
                    from,
```

**File:** contracts/src/CW721ERC721Pointer.sol (L1-32)
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
