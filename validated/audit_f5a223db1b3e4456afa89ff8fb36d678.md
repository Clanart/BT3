### Title
Reentrancy in CW1155→ERC1155 pointer `safeTransferFrom`/`safeBatchTransferFrom` via unguarded receiver callback - ([File: contracts/src/CW1155ERC1155Pointer.sol])

### Summary
`CW1155ERC1155Pointer.safeTransferFrom` and `safeBatchTransferFrom` complete the underlying CosmWasm CW1155 token move (`_execute`) and only afterward invoke an external, caller-controlled contract's `onERC1155Received`/`onERC1155BatchReceived` hook, with no reentrancy guard anywhere in the contract. This is the same bug class as the reported ProfitSplitter issue: a state-changing token transfer to an arbitrary address is followed by an unguarded external call into that same address, letting the recipient's code re-enter the pointer (or any contract built on top of it) while the outer transfer's caller/transaction is still mid-execution.

### Finding Description
`safeTransferFrom` in `contracts/src/CW1155ERC1155Pointer.sol` performs, in order: (1) a live query-based balance/approval check, (2) a delegatecall into the wasmd precompile that atomically executes the CW1155 `send` message — this is the actual value transfer, moving the token balance from `from` to `to` inside the CosmWasm module — and only then (3) calls `IERC1155Receiver(to).onERC1155Received(...)` on the arbitrary `to` address supplied by the caller: [1](#0-0) 

`safeBatchTransferFrom` follows the identical pattern — batch `_execute` first, then `onERC1155BatchReceived` callback to `to`: [2](#0-1) 

Unlike the local, in-storage OpenZeppelin ERC1155 reference implementation, this pointer contract holds no local balance mapping — `balanceOf`/`balanceOfBatch`/`isApprovedForAll` are all live cross-VM queries into the CosmWasm CW1155 contract: [3](#0-2) 

There is no `nonReentrant` modifier or any reentrancy-guard state anywhere in `CW1155ERC1155Pointer`, `CW721ERC721Pointer` (`contracts/src/CW721ERC721Pointer.sol`), or `CW20ERC20Pointer` (`contracts/src/CW20ERC20Pointer.sol`) — a pattern the repository's own test/load-generator fixtures (e.g. `FixtureReentrancyGuard` in `integration_test/load_generator/contracts/fixtures/FixtureCore.sol`) explicitly recognize is required for exactly this kind of token-transfer-then-callback flow: [4](#0-3) 

Because the CW1155 token move already happened by the time control is handed to the attacker's `onERC1155Received` implementation, and because these pointer contracts are the canonical, protocol-provided bridge that any third-party marketplace/vault contract is expected to treat as an atomic, trusted ERC1155 implementation, a malicious `to` contract can use the callback to re-enter the pointer (calling `safeTransferFrom`/`setApprovalForAll`/`burn` again with updated on-chain balances) or re-enter the calling/integrating contract (e.g. an NFT marketplace) before that contract's own listing/accounting state has been finalized — exactly the "transfers incoming tokens to `recipient`... can be re-entered" pattern flagged in the ProfitSplitter report, just crossing the EVM/CosmWasm pointer boundary instead of a plain ERC20 transfer.

### Impact Explanation
Any integrator (marketplace, vault, lending contract) built on top of the CW1155 pointer that calls `safeTransferFrom`/`safeBatchTransferFrom` to move a listed/escrowed multi-token asset to a buyer/user address is exposed to reentrancy through the buyer-controlled recipient contract, since the pointer offers no reentrancy protection despite performing an irreversible state-changing operation (the CW1155 `send`) before yielding control to untrusted code. This can result in unauthorized/duplicated transfers, double-spending of approvals, or draining of integrator contract state — i.e., unauthorized transfer via pointer / fund loss, which meets the Medium/High severity bar.

### Likelihood Explanation
Reachable by any unprivileged EVM transaction sender: an attacker only needs to deploy a contract implementing `IERC1155Receiver` and cause it to be the `to` address of a `safeTransferFrom`/`safeBatchTransferFrom` call on any CW1155 pointer (e.g. by listing/buying through a naive marketplace, or by being the direct caller themselves). No special privileges, validator control, or governance action is required.

### Recommendation
Add a reentrancy guard (e.g. OpenZeppelin's `ReentrancyGuard`) to `CW1155ERC1155Pointer.safeTransferFrom`/`safeBatchTransferFrom` (and audit `CW721ERC721Pointer`/`CW20ERC20Pointer` for the same pattern), ensuring the `nonReentrant` modifier wraps the full function including the post-`_execute` receiver-hook callback, consistent with the mitigation already applied in the repository's own `FixtureReentrancyGuard`-based load-generator contracts.

### Proof of Concept
1. Attacker deploys `MaliciousReceiver` implementing `onERC1155Received` that, when invoked, calls back into the same `CW1155ERC1155Pointer.safeTransferFrom` (or into a marketplace contract that just called `safeTransferFrom` to deliver a purchased token) before the outer call returns.
2. Attacker (or a victim marketplace acting on the attacker's behalf) calls `pointer.safeTransferFrom(from, address(MaliciousReceiver), id, amount, data)`.
3. The CW1155 `send` executes and updates CW1155 balances (`_execute` at line 63 of `contracts/src/CW1155ERC1155Pointer.sol`), then `onERC1155Received` is invoked (lines 64-75), at which point `MaliciousReceiver` re-enters — e.g., calling `pointer.setApprovalForAll`/`safeTransferFrom` again, or calling back into the marketplace contract while its listing/escrow bookkeeping for this sale is not yet finalized — enabling duplicated transfers or theft of further escrowed assets before the original transaction completes.

### Citations

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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L78-129)
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
        require(ids.length == amounts.length, "ERC1155: ids and amounts length mismatch");
        address[] memory batchFrom = new address[](ids.length);
        for (uint256 i = 0; i < ids.length; i++) {
            batchFrom[i] = from;
        }
        uint256[] memory balances = balanceOfBatch(batchFrom, ids);
        for (uint256 i = 0; i < balances.length; i++) {
            require(balances[i] >= amounts[i], "ERC1155: insufficient balance for transfer");
        }

        string memory payload = string.concat("{\"send_batch\":{\"from\":\"", AddrPrecompile.getSeiAddr(from));
        payload = string.concat(payload, "\",\"to\":\"");
        payload = string.concat(payload, AddrPrecompile.getSeiAddr(to));
        payload = string.concat(payload, "\",\"batch\":[");
        for (uint256 i = 0; i < ids.length; i++) {
            string memory batch = string.concat("{\"token_id\":\"", Strings.toString(ids[i]));
            batch = string.concat(batch, "\",\"amount\":\"");
            batch = string.concat(batch, Strings.toString(amounts[i]));
            if (i < ids.length - 1) {
                batch = string.concat(batch, "\"},");
            } else {
                batch = string.concat(batch, "\"}");
            }
            payload = string.concat(payload, batch);
        }
        payload = string.concat(payload, "]}}");
        _execute(bytes(payload));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155BatchReceived(
                    msg.sender,
                    from,
                    ids,
                    amounts,
                    data
                ) == IERC1155Receiver.onERC1155BatchReceived.selector,
                "unsafe transfer"
            );
        }
    }
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L201-261)
```text
    function balanceOf(address account, uint256 id) public view override returns (uint256) {
        require(account != address(0), "ERC1155: cannot query balance of zero address");
        string memory own = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(account)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(id)));
        string memory req = _curlyBrace(_formatPayload("balance_of", _curlyBrace(_join(own, ",", tId))));
        bytes memory response = WasmdPrecompile.query(Cw1155Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "balance");
    }

    function balanceOfBatch(
        address[] memory accounts,
        uint256[] memory ids
    ) public view override returns (uint256[] memory balances) {
        require(accounts.length != 0, "ERC1155: cannot query empty accounts list");
        if (accounts.length != ids.length) {
            revert ERC1155InvalidArrayLength(ids.length, accounts.length);
        }
        string memory ownerTokens = "[";
        for (uint256 i = 0; i < accounts.length; i++) {
            require(accounts[i] != address(0), "ERC1155: cannot query balance of zero address");
            if (i > 0) {
                ownerTokens = string.concat(ownerTokens, ",");
            }
            string memory ownerToken = string.concat("{\"owner\":\"", AddrPrecompile.getSeiAddr(accounts[i]));
            ownerToken = string.concat(ownerToken, "\",\"token_id\":\"");
            ownerToken = string.concat(ownerToken, Strings.toString(ids[i]));
            ownerToken = string.concat(ownerToken, "\"}");
            ownerTokens = string.concat(ownerTokens, ownerToken);
        }
        ownerTokens = string.concat(ownerTokens, "]");
        string memory req = _curlyBrace(_formatPayload("balance_of_batch", ownerTokens));
        bytes memory response = WasmdPrecompile.query(Cw1155Address, bytes(req));
        bytes[] memory parseResponse = JsonPrecompile.extractAsBytesList(response, "balances");
        require(parseResponse.length == accounts.length, "Invalid balance_of_batch response");
        balances = new uint256[](parseResponse.length);
        for (uint256 i = 0; i < parseResponse.length; i++) {
            balances[i] = JsonPrecompile.extractAsUint256(parseResponse[i], "amount");
        }
    }

    function uri(uint256 id) public view override returns (string memory) {
        string memory tId = _curlyBrace(_formatPayload("token_id", _doubleQuotes(Strings.toString(id))));
        string memory req = _curlyBrace(_formatPayload("token_info", tId));
        bytes memory response = WasmdPrecompile.query(Cw1155Address, bytes(req));
        return string(JsonPrecompile.extractAsBytes(response, "token_uri"));
    }

    function isApprovedForAll(address owner_, address operator) public view override returns (bool) {
        string memory own = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner_)));
        string memory op = _formatPayload("operator", _doubleQuotes(AddrPrecompile.getSeiAddr(operator)));
        string memory req = _curlyBrace(_formatPayload("is_approved_for_all", _curlyBrace(_join(own, ",", op))));
        bytes32 response = keccak256(WasmdPrecompile.query(Cw1155Address, bytes(req)));
        bytes32 approvedMsg = keccak256("{\"approved\":true}");
        bytes32 unapprovedMsg = keccak256("{\"approved\":false}");
        if (response == approvedMsg) {
            return true;
        } else if (response == unapprovedMsg) {
            return false;
        }
        revert NotImplementedOnCosmwasmContract("is_approved_for_all");
    }
```

**File:** integration_test/load_generator/contracts/fixtures/FixtureCore.sol (L62-80)
```text
abstract contract FixtureReentrancyGuard {
    bytes32 private constant GUARD_SLOT = keccak256("sei.replay.fixture.reentrancy.v1");

    modifier nonReentrant() {
        uint256 entered;
        bytes32 slot = GUARD_SLOT;
        assembly {
            entered := sload(slot)
        }
        require(entered == 0, "REENTRANCY");
        assembly {
            sstore(slot, 1)
        }
        _;
        assembly {
            sstore(slot, 0)
        }
    }
}
```
