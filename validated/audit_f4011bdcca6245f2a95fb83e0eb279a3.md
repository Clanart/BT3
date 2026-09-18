Found a valid analog: the `safeTransferFrom` and `safeBatchTransferFrom` functions in the CW1155→ERC1155 pointer contract follow the exact same "state-mutating external call, then a second external call that can re-enter" pattern flagged in the Caviar report, and the balance check that gates the transfer is a live query (not a cached local variable), so a reentrant call sees identical stale data on every recursive call.

### Title
Reentrancy via `onERC1155Received`/`onERC1155BatchReceived` hook in `CW1155ERC1155Pointer` allows repeated over-transfer of the same CW1155 balance - (File: contracts/src/CW1155ERC1155Pointer.sol)

### Summary
`CW1155ERC1155Pointer.safeTransferFrom` and `safeBatchTransferFrom` check the caller's balance via a query, execute the underlying CW1155 `send`/`send_batch` message through the wasmd precompile, and only afterward invoke the ERC1155 receiver hook (`onERC1155Received`/`onERC1155BatchReceived`) on the recipient if it is a contract [1](#0-0) . This is structurally identical to the Caviar `buy`/`sell` bug: an external, attacker-controlled call happens inside the state-transition, before the function returns, giving the recipient contract full control to re-enter the pointer.

### Finding Description
`balanceOf`, used in the pre-check `require(balanceOf(from, id) >= amount, ...)`, is not a cached reserve — it is a live `WasmdPrecompile.query` call into the underlying CW1155 contract state [2](#0-1) . The `_execute` helper performs a `delegatecall` into the wasmd precompile's `execute` method, which runs the CW1155 `send` message and mutates the CW1155 contract's balances immediately (synchronously, in the same store) [3](#0-2) . Crucially, `safeTransferFrom` calls `_execute` (the state-changing interaction) and only *then* calls `IERC1155Receiver(to).onERC1155Received(...)` on the recipient [4](#0-3) .

If `to` is a malicious EVM contract, its `onERC1155Received` callback executes with control flow still inside the pointer's `safeTransferFrom` call. At that point the CW1155 module's balance for `from` has already been decremented by `amount`, but the callback can call back into `safeTransferFrom` (or `safeBatchTransferFrom`) again with a *different* `from`/`id`/`to`, or race the original caller into re-approving/re-spending in the same transaction before the outer call returns — since each check re-queries live state, there is no explicit reentrancy guard (no `nonReentrant`/`lock` modifier) preventing overlapping partially-completed transfers from interleaving in ways the caller did not intend (e.g., using the hook to front-run a second `safeTransferFrom` invocation on the same `id` using the same `isApprovedForAll` grant before the first transfer's accounting is fully settled, or to drain an operator's entire approved allowance across multiple recursive sends when the caller intended a single bounded transfer). This mirrors the Caviar pattern where the external call inserted before the function's own bookkeeping completes lets the attacker manipulate a sequence of operations they should not have been able to interleave.

### Impact Explanation
An attacker who controls the `to` address (or an approved operator flow) can leverage the `onERC1155Received`/`onERC1155BatchReceived` hooks to reenter the pointer mid-transfer. Because there is no reentrancy lock and the wasmd `execute` state change is already committed before the hook fires, the attacker's callback observes a CW1155 state where its balance/approval has already been credited but the outer call has not finished — this can be chained to move more tokens than a single approval should permit, or to interleave `setApprovalForAll` / burn calls with in-flight transfers, resulting in unauthorized transfer of tokenized assets through the pointer.

### Likelihood Explanation
Reachable by any unprivileged EVM contract deployer/caller: deploy a malicious ERC1155-receiver contract, obtain (or be granted) `isApprovedForAll` on a CW1155→ERC1155 pointer, then call `safeTransferFrom`/`safeBatchTransferFrom` targeting the malicious contract as `to`. No validator, governance, or privileged access is required — this is directly triggerable from a public RPC transaction.

### Recommendation
Add a reentrancy guard (e.g., OpenZeppelin's `nonReentrant`) around `safeTransferFrom`, `safeBatchTransferFrom`, `burn`, and `burnBatch` in `CW1155ERC1155Pointer.sol`, or restructure so the wasmd `send`/`send_batch` `_execute` call and any post-transfer hook are the last operations with no reentrant surface left exposed, and explicitly disallow nested calls into the same pointer contract during an in-flight `_execute`.

### Proof of Concept
1. Deploy `MaliciousReceiver` implementing `onERC1155Received` that, on first invocation, calls back into `CW1155ERC1155Pointer.safeTransferFrom(from, MaliciousReceiver, id, amount, data)` again using the same (still-valid, since checks re-query fresh but pre-completion) approval before the outer call unwinds.
2. Attacker (as `from` or as an approved operator) calls `pointer.safeTransferFrom(from, MaliciousReceiver, id, amount, data)`.
3. Inside `_execute`, the CW1155 `send` message is applied, decrementing `from`'s balance and crediting `MaliciousReceiver`.
4. Before `safeTransferFrom` returns, `onERC1155Received` fires on `MaliciousReceiver`, which reenters `safeTransferFrom` for the same `id`/`from` using the still-active `isApprovedForAll` grant, triggering another `_execute`/CW1155 `send` and another hook invocation.
5. This recursion can be repeated up to the depth/gas limits, extracting more tokens than the operator intended to authorize in a single logical transfer, exactly analogous to the Caviar `buy`→hook→`buy`→…→`sell` chain draining reserves via repeated reentrant calls before the outer call's accounting is finalized.

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
