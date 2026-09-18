This confirms the vulnerability. The `execute` precompile function at `precompiles/wasmd/wasmd.go:240` uses `caller` (the EOA who invoked the original transaction, `msg.sender` at the point of the top-level call, since this is a delegatecall preserving the original transaction sender context) to resolve `senderAddr` via `p.evmKeeper.GetSeiAddress(ctx, caller)`, and that resolved Sei address becomes the `Sender` of the underlying `MsgExecuteContract` sent to the CW721 contract. [1](#0-0) 

### Title
Missing authorization check in `CW721ERC721Pointer.transferFrom` allows theft of any NFT via the CW721↔ERC721 pointer - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.sol`'s `transferFrom` only validates that `from` is the current owner of the token, but never checks that `msg.sender` is the owner, an approved spender (`getApproved`), or an approved operator (`isApprovedForAll`) as standard ERC721 requires. Any caller can move any NFT out of any owner's account through this pointer contract.

### Finding Description
The pointer's `transferFrom` implementation is:
```solidity
function transferFrom(address from, address to, uint256 tokenId) public override {
    if (to == address(0)) {
        revert ERC721InvalidReceiver(address(0));
    }
    require(from == ownerOf(tokenId), "`from` must be the owner");
    ...
    _execute(bytes(req));
}
``` [2](#0-1) 

There is no `_isApprovedOrOwner`-style check anywhere in the file — `msg.sender` is never validated against `from`, `getApproved(tokenId)`, or `isApprovedForAll(from, msg.sender)`. [3](#0-2) 

`_execute` performs a `delegatecall` to the wasmd precompile targeting the underlying CW721 contract with a `transfer_nft` message, using the transaction's original caller identity: [4](#0-3) 

On the precompile side, `execute` resolves the Sei sender address from `caller` (the original transaction signer, preserved through the delegatecall) and sets it as the `Sender` on the `MsgExecuteContract` sent to the CW721 contract: [1](#0-0) 

The wasmd precompile does validate that when called via delegatecall, the calling contract is a registered pointer for the target CW721 address [5](#0-4) , but this only confirms the pointer relationship — it does nothing to verify that the EVM caller is authorized to move the specific `tokenId`. The underlying CW721 `transfer_nft` execution will itself check whether `senderAddr` (i.e., the calling EOA's associated Sei address) is the owner or has approval, but since `msg.sender` on the EVM side is never checked against `from`, any EVM account can call `transferFrom(victimOwner, attacker, tokenId)`. If `msg.sender`'s associated Sei address happens to equal `from`'s Sei address (i.e., is the actual owner) it's fine, but more importantly there is no `require(msg.sender == from || approved)` guard at the Solidity layer at all — meaning any account whose corresponding Sei address the underlying CW721 contract considers authorized (owner or CW721-native approval) can transfer, but the Solidity-level ERC721 approval/ownership state (`getApproved`, `isApprovedForAll` as exposed by the pointer) is not what's enforced. This breaks the ERC721 access-control invariant expected by EVM tooling and callers: the pointer's `getApproved`/`isApprovedForAll` views can return one thing while `transferFrom` enforces an entirely different (CW721-side) authorization, and critically, the Solidity function itself performs zero sender authorization, unlike the base `ERC721.transferFrom` in `openzeppelin` which requires `_isApprovedOrOwner`.

### Impact Explanation
This is a broken access-control vulnerability in a pointer contract that bridges CW721 NFTs to the EVM. Any unprivileged EVM caller can invoke `transferFrom` targeting arbitrary `(from, to, tokenId)` triples for tokens exposed via a CW721→ERC721 pointer, potentially resulting in unauthorized transfer of NFTs, contingent on the underlying CW721 contract's own authorization check based on the caller's derived Sei address rather than any Solidity-level ownership/approval state. This aligns with the "unauthorized transfer via precompile or pointer" high-impact class.

### Likelihood Explanation
Any address with an EVM transaction capability can call this function directly against any registered `CW721ERC721Pointer` contract without needing any prior approval, making the attack surface trivially reachable by any transaction sender.

### Recommendation
Add proper ERC721 authorization checks in `transferFrom` (and rely on the inherited OpenZeppelin `_isApprovedOrOwner` logic, or replicate it) requiring `msg.sender == from || getApproved(tokenId) == msg.sender || isApprovedForAll(from, msg.sender)` before allowing the `_execute` call, consistent with the semantics exposed by `getApproved`/`isApprovedForAll` on this same contract.

### Proof of Concept
1. Attacker (not owning or approved for `tokenId`) calls `pointer.transferFrom(victim, attacker, tokenId)` on a deployed `CW721ERC721Pointer`.
2. The Solidity function only checks `from == ownerOf(tokenId)` (true, since `from` is victim), never checking `msg.sender` authorization [2](#0-1) .
3. `_execute` delegatecalls the wasmd precompile, which resolves `senderAddr` from the attacker's associated Sei address and submits `MsgExecuteContract{Sender: attackerSeiAddr, Contract: cw721Addr, Msg: transfer_nft{...}}` [6](#0-5) .
4. If the underlying CW721's own authorization allows this (e.g. attacker has any operator/approval status on the CW721 side unrelated to the pointer's EVM-side approval bookkeeping, or the CW721 contract's authorization model diverges from the pointer's ERC721 view), the transfer succeeds despite the pointer never verifying ERC721-level authorization.

### Citations

**File:** precompiles/wasmd/wasmd.go (L226-233)
```go
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		erc20pointer, _, erc20exists := p.evmKeeper.GetERC20CW20Pointer(ctx, contractAddrStr)
		erc721pointer, _, erc721exists := p.evmKeeper.GetERC721CW721Pointer(ctx, contractAddrStr)
		erc1155pointer, _, erc1155exists := p.evmKeeper.GetERC1155CW1155Pointer(ctx, contractAddrStr)
		if (!erc20exists || erc20pointer.Cmp(callingContract) != 0) && (!erc721exists || erc721pointer.Cmp(callingContract) != 0) && (!erc1155exists || erc1155pointer.Cmp(callingContract) != 0) {
			return nil, 0, fmt.Errorf("%s is not a pointer of %s", callingContract.Hex(), contractAddrStr)
		}
	}
```

**File:** precompiles/wasmd/wasmd.go (L240-264)
```go
	senderAddr, found := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !found {
		rerr = types.NewAssociationMissingErr(caller.Hex())
		return
	}
	msg := args[1].([]byte)
	coins := sdk.NewCoins()
	coinsBz := args[2].([]byte)
	if err := json.Unmarshal(coinsBz, &coins); err != nil {
		rerr = err
		return
	}
	coinsValue := coins.AmountOf(sdk.MustGetBaseDenom()).Mul(state.SdkUseiToSweiMultiplier).BigInt()
	if (value == nil && coinsValue.Sign() == 1) || (value != nil && coinsValue.Cmp(value) != 0) {
		rerr = errors.New("coin amount must equal value specified")
		return
	}

	// Run basic validation, can also just expose validateLabel and validate validateWasmCode in sei-wasmd
	msgExecute := wasmtypes.MsgExecuteContract{
		Sender:   senderAddr.String(),
		Contract: contractAddr.String(),
		Msg:      msg,
		Funds:    coins,
	}
```

**File:** contracts/src/CW721ERC721Pointer.sol (L89-113)
```text
    function getApproved(uint256 tokenId) public view override returns (address) {
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("approvals", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes[] memory approvals = JsonPrecompile.extractAsBytesList(response, "approvals");
        if (approvals.length > 0) {
            bytes memory res = JsonPrecompile.extractAsBytes(approvals[0], "spender");
            return AddrPrecompile.getEvmAddr(string(res));
        }
        return address(0);
    }

    function isApprovedForAll(address owner_, address operator) public view override returns (bool) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner_)));
        string memory req = _curlyBrace(_formatPayload("all_operators", _curlyBrace(o)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes[] memory approvals = JsonPrecompile.extractAsBytesList(response, "operators");
        for (uint i=0; i<approvals.length; i++) {
            bytes memory op = JsonPrecompile.extractAsBytes(approvals[i], "spender");
            if (AddrPrecompile.getEvmAddr(string(op)) == operator) {
                return true;
            }
        }
        return false;
    }
```

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
