### Title
`CW20ERC20Pointer.transfer`/`transferFrom` unconditionally revert when the recipient is unassociated, breaking composability - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer` and the sibling `CW1155ERC1155Pointer`/`CW721ERC721Pointer` contracts wrap a CosmWasm CW20 token as an ERC20 on the EVM side. Their `transfer()`/`transferFrom()` implementations resolve the EVM `to`/`from` addresses to Sei bech32 addresses via the `Addr` precompile before forwarding the CW20 `transfer`/`transfer_from` message through the `wasmd` precompile. If the target address has not yet been associated with a Sei address, this resolution reverts the entire call, exactly analogous to the reported `GClaimManager`/`Divider._collect()` pattern where an internal dependency call can unconditionally revert and propagate the failure up through `transfer()`/`transferFrom()`.

### Finding Description
`transfer()` calls `AddrPrecompile.getSeiAddr(to)` to build the CW20 message payload before executing the underlying wasm call: [1](#0-0) 

`transferFrom()` does the same for both `from` and `to`: [2](#0-1) 

The underlying execution helper also reverts unconditionally on failure: [3](#0-2) 

This precisely mirrors the reported bug class: a standard ERC20-style `transfer`/`transferFrom` function that is supposed to be composable with arbitrary DeFi contracts (routers, vaults, marketplaces, aggregators) instead unconditionally reverts whenever an internal dependency check fails — here, when the destination or source address has not completed Sei<->EVM address association. This is confirmed by the existing test suite, which explicitly documents that a transfer to an unassociated address fails: [4](#0-3) [5](#0-4) 

Because any EVM contract that has never sent or received a transaction on Sei is by default unassociated, any protocol built on top of a CW20 pointer token (AMMs, lending markets, NFT marketplaces settling in this token, aggregator routers, etc.) can have `transfer`/`transferFrom` legs of a larger transaction fail purely because the recipient (which could be a legitimate newly-deployed contract or a fresh EOA) has not yet been associated — with no ability for the caller to detect or work around this ahead of time within the same transaction, since association itself normally requires a separate prior transaction from that address.

### Impact Explanation
This breaks composability for any composed transaction that includes a `CW20ERC20Pointer` transfer leg targeting an unassociated address: the whole transaction reverts, which can strand funds mid-flow in multi-step routers/aggregators, cause failed settlements in marketplaces, or block automated systems (e.g., liquidations, keeper bots) from completing time-sensitive operations. It does not directly cause fund loss or permanent freezing on its own, but it is a systemic reliability/DoS-style hazard for any contract that composes with these pointer tokens, consistent with the "Medium Risk" severity the original report assigned to the analogous `GClaimManager` issue.

### Likelihood Explanation
High likelihood of being hit in practice: any EVM contract or freshly-created EOA that has not previously transacted on Sei is unassociated by default, so any router/aggregator/marketplace that attempts to move a CW20 pointer token to a new contract address or new user will trigger this revert deterministically, not merely under adversarial conditions.

### Recommendation
Reconsider unconditionally reverting inside `transfer()`/`transferFrom()` when address association is missing. Options include: returning `false` per the classic (non-reverting) ERC20 semantics instead of reverting, exposing a way to pre-check association status so callers can guard against it, or auto-associating recipients where safe, so that composability with downstream contracts is preserved.

### Proof of Concept
The existing repository test suite already demonstrates the revert path: [4](#0-3) 
A router/aggregator contract that calls `CW20ERC20Pointer.transfer(newContractAddress, amount)` or `transferFrom(unassociatedUser, ..., amount)` as one leg of a multi-step transaction will have the entire transaction revert if `newContractAddress`/`unassociatedUser` has not yet been associated, since `AddrPrecompile.getSeiAddr` is called unconditionally before the wasm `transfer`/`transfer_from` message is dispatched.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L79-86)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L88-96)
```text
    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```

**File:** contracts/test/ERC20toCW20PointerTest.js (L143-146)
```javascript
                it("transfer to unassociated address should fail", async function () {
                    const unassociatedRecipient = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";
                    await expect(pointer.transfer(unassociatedRecipient, 1)).to.be.revertedWithoutReason;
                });
```

**File:** contracts/test/CW20toERC20PointerTest.js (L118-128)
```javascript
                it("transfer to unassociated address should fail", async function() {
                    const unassociatedSeiAddr = "sei1z7qugn2xy4ww0c9nsccftxw592n4xhxccmcf4q";
                    const respBefore = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceBefore = respBefore.data.balance;

                    await executeWasm(pointer,  { transfer: { recipient: unassociatedSeiAddr, amount: "100" } });
                    const respAfter = await queryWasm(pointer, "balance", {address: accounts[1].seiAddress});
                    const balanceAfter = respAfter.data.balance;

                    expect(balanceAfter).to.equal(balanceBefore);
                });
```
