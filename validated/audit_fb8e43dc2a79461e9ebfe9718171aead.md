Confirmed finding: `CW721ERC721Pointer.sol` `transferFrom` performs **no on-chain caller-authorization check** — it only verifies `from == ownerOf(tokenId)` and then blindly executes a CosmWasm `transfer_nft` message, relying entirely on the CW721 contract to reject unauthorized callers. Combined with the fact that the standard OpenZeppelin `ERC721` authorization/ownership bookkeeping (`_ownerOf`, `_balanceOf`, `_tokenApprovals`, `_operatorApprovals`, `_isAuthorized`, `_update`) is completely unused/dead because every relevant view (`ownerOf`, `balanceOf`, `getApproved`, `isApprovedForAll`) and every relevant mutator (`transferFrom`, `approve`, `setApprovalForAll`) is overridden to talk to the CW721 contract via the `wasmd` precompile, this is a direct structural analog to the reported `DebtToken` bug: the derived contract "redefines" the base NFT's ownership-tracking surface to point at an external registry instead of layering on top of it (`super._setTokenOwner()`-style pattern), so none of Solidity's own authorization primitives are ever consulted.

### Title
Owner-tracking duplication in CW721ERC721Pointer bypasses OpenZeppelin authorization checks, relying solely on precompile passthrough - (File: contracts/src/CW721ERC721Pointer.sol)

### Summary
`CW721ERC721Pointer` extends OpenZeppelin `ERC721`/`ERC2981` but entirely redefines the ownership/approval surface (`ownerOf`, `balanceOf`, `getApproved`, `isApprovedForAll`, `transferFrom`, `approve`, `setApprovalForAll`) to proxy to the underlying CW721 contract through the `IWasmd` precompile, exactly like the `DebtToken` pattern of redefining `_setTokenOwner`/`_ownerOf` to point at an external `DebtRegistry` instead of layering on the base implementation.

### Finding Description
`transferFrom` is defined as: [1](#0-0) 
It checks only `from == ownerOf(tokenId)` and never checks that `msg.sender` is the owner, an approved address (`getApproved`), or an approved operator (`isApprovedForAll`) — a check that OpenZeppelin's base `ERC721.transferFrom`/`_update`/`_isAuthorized` would normally enforce. Likewise, `approve` and `setApprovalForAll` perform no local authorization check before dispatching the CosmWasm execute message: [2](#0-1) 
All EVM-side ownership/approval state (`_ownerOf`, `_balanceOf`, `_tokenApprovals`, `_operatorApprovals` inherited from OpenZeppelin `ERC721`) is dead code — never read or written — because `ownerOf`/`balanceOf`/`getApproved`/`isApprovedForAll` are all overridden to query the CW721 contract directly: [3](#0-2) 
This mirrors the `DebtToken` root cause precisely: instead of layering pointer-specific logic on top of the base contract's authorization/ownership tracking (e.g., calling `super._update`/`super._isAuthorized` in addition to the CW721 registry calls), the pointer entirely replaces that logic and pushes 100% of the security-critical authorization decision onto the destination system (`_execute` → `WasmdPrecompile.execute`).

### Impact Explanation
Whether this is exploitable for unauthorized transfer/approval depends entirely on whether the wasmd precompile's `execute()` correctly derives the CosmWasm sender from the EVM `msg.sender` (via Sei/EVM address association) when routing the `transfer_nft`/`approve`/`approve_all` messages, and whether the CW721 contract's own message handlers correctly reject a `from`/`spender` that isn't the real Sei-side owner or approved operator. I was not able to fully trace the sender-derivation path inside the `wasmd` precompile's `execute` implementation within the remaining investigation budget, so I cannot confirm with certainty whether a caller who is not the actual owner/approved party could get an unauthorized transfer executed through this pointer. If the underlying CW721 message handler enforces the correct sender check, the missing Solidity-level check is a defense-in-depth/code-quality gap rather than an exploitable bug (matching the original report's characterization as "obscure hard-to-spot issues" rather than an immediate loss). I therefore cannot assert with the required confidence that this reaches "unauthorized transfer via precompile or pointer" as a proven, concrete impact — it is a plausible but unconfirmed root cause requiring further tracing of `precompiles/wasmd` sender-association logic and the CW721 reference contract's authorization checks, which was outside what I could verify in this pass.

### Likelihood Explanation
Not established with confidence — depends on unverified precompile sender-derivation behavior.

### Recommendation
If this analysis is to be pursued, verify how `WASMD_PRECOMPILE_ADDRESS.delegatecall("execute", ...)` derives the CosmWasm message sender from the calling EVM address, and confirm the CW721 contract enforces owner/approved-operator checks against that derived sender for `transfer_nft`, `approve`, `approve_all`, and `revoke_all`. Regardless, add explicit Solidity-side authorization checks (`msg.sender == owner || msg.sender == getApproved(tokenId) || isApprovedForAll(owner, msg.sender)`) in `transferFrom`/`approve`/`setApprovalForAll` so pointer-level security does not depend solely on the precompile/CosmWasm layer, consistent with the original report's suggested fix of layering (not replacing) the base contract's ownership semantics.

### Proof of Concept
Not constructed — a concrete PoC requires confirming the sender-association behavior of the `wasmd` precompile's `execute` path and the CW721 reference contract's permission checks, which I could not fully verify within the available tool budget.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L51-113)
```text
    function balanceOf(address owner_) public view override returns (uint256) {
        if (owner_ == address(0)) {
            revert ERC721InvalidOwner(address(0));
        }
        uint256 numTokens = 0;
        string memory startAfter;
        string memory qb = string.concat(
            string.concat("\"limit\":1000,\"owner\":\"", AddrPrecompile.getSeiAddr(owner_)),
            "\""
        );
        bytes32 terminator = keccak256("{\"tokens\":[]}");

        bytes[] memory tokens;
        uint256 tokensLength;
        string memory req = string.concat(string.concat("{\"tokens\":{", qb), "}}");
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        while (keccak256(response) != terminator) {
            tokens = JsonPrecompile.extractAsBytesList(response, "tokens");
            tokensLength = tokens.length;
            numTokens += tokensLength;
            startAfter = string.concat(",\"start_after\":", string(tokens[tokensLength-1]));
            req = string.concat(
                string.concat("{\"tokens\":{", string.concat(qb, startAfter)),
                "}}"
            );
            response = WasmdPrecompile.query(Cw721Address, bytes(req));
        }
        return numTokens;
    }

    function ownerOf(uint256 tokenId) public view override returns (address) {
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("owner_of", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes memory owner_ = JsonPrecompile.extractAsBytes(response, "owner");
        return AddrPrecompile.getEvmAddr(string(owner_));
    }

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

**File:** contracts/src/CW721ERC721Pointer.sol (L171-185)
```text
    function approve(address approved, uint256 tokenId) public override {
        string memory spender = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(approved)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("approve", _curlyBrace(_join(spender, tId, ","))));
        _execute(bytes(req));
    }

    function setApprovalForAll(address operator, bool approved) public override {
        string memory op = _curlyBrace(_formatPayload("operator", _doubleQuotes(AddrPrecompile.getSeiAddr(operator))));
        if (approved) {
            _execute(bytes(_curlyBrace(_formatPayload("approve_all", op))));
        } else {
            _execute(bytes(_curlyBrace(_formatPayload("revoke_all", op))));
        }
    }
```
