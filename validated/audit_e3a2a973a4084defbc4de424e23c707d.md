Confirmed: in `execute()` at `precompiles/wasmd/wasmd.go:240`, `senderAddr` is derived from `caller` (the original `msg.sender` preserved through `delegatecall`), so the underlying CW721 `transfer_nft` message is submitted as the actual EOA that called `transferFrom`, not as the pointer contract. The CW721 contract's own `transfer_nft` handler enforces that the sender is the token owner or an approved spender/operator, which is state that mirrors what `CW721ERC721Pointer.sol`'s `getApproved`/`isApprovedForAll` expose. This means unauthorized calls are ultimately rejected by the CW721 contract itself, not by the Solidity wrapper — the missing `_isApprovedOrOwner` check in `CW721ERC721Pointer.sol`'s `transferFrom` is real but its exploitability depends on whether the CW721 contract enforces authorization identically, which I could not fully verify (CW721 contract implementation is outside indexed Go/Solidity code and I could not trace its exact `transfer_nft` execute handler).

### Title
Missing local authorization check in `CW721ERC721Pointer.transferFrom` - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer.sol`, the ERC721-compatible EVM pointer wrapper around a CW721 contract, overrides `transferFrom` but omits the `_isApprovedOrOwner`-style authorization check that standard ERC721 implementations use (compare with `contracts/src/ERC721.sol` and `loadtest/contracts/evm/src/ERC721.sol`, which both explicitly `require(_isApprovedOrOwner(from, msg.sender, id), "not authorized")`).

### Finding Description
`transferFrom` in `contracts/src/CW721ERC721Pointer.sol` only checks `require(from == ownerOf(tokenId), ...)` [1](#0-0)  and never verifies that `msg.sender` is `from`, an address approved via `getApproved`, or an operator via `isApprovedForAll`. It then forwards a `transfer_nft` CosmWasm execute message via `delegatecall` to the wasmd precompile [2](#0-1) . In the wasmd precompile's `execute()`, the CW message sender is resolved from `caller`, which under `delegatecall` is the original EOA `msg.sender` (the arbitrary caller of `transferFrom`), not the pointer contract's own address [3](#0-2) . Whether this is actually exploitable depends entirely on whether the underlying CW721 contract's own `transfer_nft` handler independently re-validates that the message sender is the token owner or has an approval/operator grant recorded in CW721 state — this is standard behavior for CW721 implementations (including `cw721-base`), which would reject an unauthorized sender at the wasm layer even though the Solidity wrapper failed to check it.

### Impact Explanation
If the underlying CW721 contract enforces its own authorization on `transfer_nft` (as `cw721-base` and CW721-spec compliant contracts do), this bug is not independently exploitable — it is defense-in-depth missing at the EVM layer, with the CW721 layer as a backstop. I was not able to inspect the actual CW721 contract execute-message handler in this repo (likely implemented in `sei-wasmd`/external CW721 contracts, which is outside the indexed content) to confirm this backstop exists for every code path (e.g., custom or third-party CW721 contracts wrapped by a pointer that do not enforce sender authorization identically to `getApproved`/`isApprovedForAll` semantics). If such a mismatch exists for any wrapped CW721 contract, an attacker could call `transferFrom(owner, attacker, tokenId)` for any token without being the owner or approved, resulting in unauthorized transfer/fund loss.

### Likelihood Explanation
Low-to-moderate confidence without confirming the underlying CW721 contract behavior. Given that CW721 contracts almost universally validate `info.sender` against `Approval`/`Operators` maps in `transfer_nft`, this is likely dependent on the specific CW721 implementation being pointed to, and may not be exploitable against the standard `cw721-base` contract used elsewhere in the codebase's tests.

### Recommendation
Add an explicit `_isApprovedOrOwner`/`require` check in `CW721ERC721Pointer.sol`'s `transferFrom` matching the pattern used in `contracts/src/ERC721.sol` [4](#0-3) , so the EVM-side wrapper does not rely solely on the CW721 contract's own authorization enforcement, which is not guaranteed to be enforced identically to the ERC721 `getApproved`/`isApprovedForAll` semantics exposed by this pointer contract.

### Proof of Concept
Not able to construct a concrete end-to-end PoC without confirming the exact CW721 contract execute-handler semantics for `transfer_nft`, which is required to determine if the missing Solidity-level authorization check is actually reachable as an unauthorized transfer versus being blocked by the CW721 contract's own sender validation.

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

**File:** contracts/src/ERC721.sol (L121-125)
```text
    function transferFrom(address from, address to, uint id) public {
        require(from == _ownerOf[id], "from != owner");
        require(to != address(0), "transfer to zero address");

        require(_isApprovedOrOwner(from, msg.sender, id), "not authorized");
```
