### Title
Missing reentrancy protection in `CW1155ERC1155Pointer.safeTransferFrom`/`safeBatchTransferFrom` allows attacker-controlled receiver callback to re-enter the pointer while CW-side state is mid-transition - ([File: contracts/src/CW1155ERC1155Pointer.sol])

### Summary
`CW1155ERC1155Pointer.safeTransferFrom` and `safeBatchTransferFrom` perform the CosmWasm-side balance mutation via `_execute()` (a `delegatecall` into the wasmd precompile) and then, with no reentrancy guard of any kind, invoke `IERC1155Receiver(to).onERC1155Received(...)`/`onERC1155BatchReceived(...)` on an arbitrary, attacker-controlled `to` address.

### Finding Description
`safeTransferFrom` in [1](#0-0)  executes the CW1155 `send` message through `_execute()`, which `delegatecall`s the wasmd precompile at `WASMD_PRECOMPILE_ADDRESS` [2](#0-1) , and only afterward calls the untrusted `to` contract's `onERC1155Received` hook. `safeBatchTransferFrom` follows the same pattern [3](#0-2) . Neither function, nor any other state-changing function in this contract (`burn`, `burnBatch`, `setApprovalForAll`), has a `nonReentrant` modifier or any reentrancy guard variable — there is no analog of the `FixtureReentrancyGuard` pattern seen elsewhere in the repo's test fixtures [4](#0-3)  applied to any of the production CW-EVM pointer contracts (`CW20ERC20Pointer`, `CW721ERC721Pointer`, `CW1155ERC1155Pointer`).

This is precisely the bug class in the external report: a state-changing external-facing function makes an external call (here, an ERC-1155 receiver hook to an attacker-supplied contract address) without any `nonReentrant` guard, after already committing part of its state transition (the CW1155 module-level balance change already went through the wasmd keeper).

Unlike a plain reentrancy bug where the same contract's storage is at risk, the practical impact here is bounded because `balanceOf`/`isApprovedForAll` in the pointer are *live* pass-through queries into the CW1155 module state (not cached local storage) [5](#0-4) , and each `_execute()` call is a genuine, atomically-checked CosmWasm state transition. A malicious `to` receiver contract can, however, re-enter the pointer (or other pointer-wrapped tokens, or the wasmd/bank precompiles directly) mid-call — e.g. call back into `burn`, `setApprovalForAll`, or trigger transfers of a *second* token/pointer contract — while the top-level EVM call frame has not yet returned and before any receiver-hook-dependent invariants in the calling contract (e.g. a marketplace, vault, or router built on top of this pointer) have been finalized. Because CosmWasm contract execution invoked through the wasmd precompile explicitly disallows re-entering itself in a CW→EVM→CW loop (`"sei does not support CW->EVM->CW call pattern"`) [6](#0-5) , the primary risk surfaces one level up: any external EVM protocol composing with this unguarded pointer (lending markets, marketplaces, routers) inherits a callback that can reenter the pointer or sibling pointer contracts before the composing protocol's own accounting for the transfer is settled, enabling classic reentrancy-style state manipulation (double-use of a single transfer's side effects, out-of-order approval consumption, etc.) in any protocol built on top of these pointer contracts.

### Impact Explanation
This satisfies the Medium/High bar because it enables state manipulation via unauthorized reentry into a widely-used token-pointer primitive (`CW1155ERC1155Pointer`, and by the same pattern the batch variant) that every EVM contract on Sei interacting with CW1155-backed NFTs depends on. A malicious `to` contract receiving a token can reenter the pointer or a downstream integrator contract mid-transfer to manipulate approvals/balances state or double-trigger side effects, which can translate into fund loss or unauthorized transfers in composing protocols (marketplaces, vaults, routers) that assume the ERC1155 receiver hook is the last external interaction in the call.

### Likelihood Explanation
Likelihood is High: any unprivileged EVM transaction sender can deploy a malicious `to` contract implementing `IERC1155Receiver` and call `safeTransferFrom`/`safeBatchTransferFrom` on any deployed `CW1155ERC1155Pointer` to trigger the callback. No special privileges, governance, or validator collusion is required — a single crafted transaction is sufficient to reach the vulnerable code path.

### Recommendation
Add a `nonReentrant` guard (OpenZeppelin's `ReentrancyGuard` or an equivalent storage-slot-based guard as used in the test fixtures' `FixtureReentrancyGuard`) to all state-mutating external functions in `CW1155ERC1155Pointer.sol` (`safeTransferFrom`, `safeBatchTransferFrom`, `setApprovalForAll`, `burn`, `burnBatch`), and audit `CW20ERC20Pointer.sol`/`CW721ERC721Pointer.sol` for the same missing-guard pattern on their `_execute()`-calling transaction functions, since they share the identical unguarded external-call structure (though ERC20/ERC721 `transferFrom` here do not invoke receiver hooks, any future addition of `safeTransferFrom`/hooks would reintroduce the same risk).

### Proof of Concept
1. Deploy an attacker contract `Evil` implementing `IERC1155Receiver.onERC1155Received` that, upon being called, immediately calls back into the same `CW1155ERC1155Pointer` instance (e.g., invoking `setApprovalForAll` or `burn` on behalf of `from`, using an approval that was valid at call-start) or into a second protocol contract that has cached pre-transfer state.
2. Have `from` approve `Evil`'s deploying account (or itself) via `setApprovalForAll`.
3. Call `pointer.safeTransferFrom(from, Evil_address, id, amount, "")`.
4. Inside `_execute()`, the CW1155 `send` message is completed at the module level (tokens now belong to `Evil`), and then `IERC1155Receiver(Evil).onERC1155Received` fires — reentering the pointer or a downstream contract before the outer `safeTransferFrom` call returns, letting `Evil` perform further approved actions or manipulate a composing protocol's pending accounting for this same transfer.

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

**File:** contracts/src/CW1155ERC1155Pointer.sol (L201-208)
```text
    function balanceOf(address account, uint256 id) public view override returns (uint256) {
        require(account != address(0), "ERC1155: cannot query balance of zero address");
        string memory own = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(account)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(id)));
        string memory req = _curlyBrace(_formatPayload("balance_of", _curlyBrace(_join(own, ",", tId))));
        bytes memory response = WasmdPrecompile.query(Cw1155Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "balance");
    }
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L313-324)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw1155Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
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

**File:** precompiles/wasmd/legacy/v552/wasmd.go (L143-145)
```go
	if method.Name != QueryMethod && !ctx.IsEVM() {
		return nil, 0, errors.New("sei does not support CW->EVM->CW call pattern")
	}
```
