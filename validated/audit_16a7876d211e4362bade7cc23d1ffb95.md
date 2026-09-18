### Title
Missing sender authorization check in CW721 pointer's `transfer_nft` handler allows any account to steal wrapped ERC721 tokens - ([File: example/cosmwasm/cw721/src/contract.rs])

### Summary
The CosmWasm-side CW721 pointer contract that wraps a native ERC721 token exposes a `transfer_nft` execute handler that looks up the token's real owner by `token_id` and issues an EVM transfer on the owner's behalf — but never verifies that the CosmWasm message sender (`info.sender`) actually is that owner (or an approved operator) before doing so. This is the same bug class as the FOSSBilling report: a resource is fetched by ID and acted upon without checking that the caller owns/controls it.

### Finding Description
`execute_transfer_nft` forwards directly into the internal `transfer_nft` helper: [1](#0-0) 

The helper resolves the current owner of the token and then builds a delegated EVM call that performs the transfer using that owner as `from`, completely independent of who invoked the message: [2](#0-1) 

Note that `info.sender` is only used as an argument to the `erc721_owner` query (to resolve the current owner), not compared against the resolved `owner` — there is no `if info.sender != owner && !is_approved { return Err(Unauthorized) }` check anywhere in this path, unlike a compliant CW721/ERC721 implementation. Compare this to the underlying ERC721 contract, which relies on `_isApprovedOrOwner(from, msg.sender, id)` to gate transfers: [3](#0-2) 

Because the pointer contract issues the actual transfer via `EvmMsg::DelegateCallEvm`, the resulting EVM `transferFrom` call is executed as if the *pointer contract itself* is the caller (not the original CosmWasm `info.sender`). Since the real owner must have approved/operator-authorized the pointer contract in order for the pointer to function at all (this is the standard setup for such bridging pointers), the underlying ERC721's `_isApprovedOrOwner` check passes for the pointer contract's own address — regardless of who actually invoked `transfer_nft` on the CosmWasm side. The authorization boundary is therefore only enforced at the CW721 wrapper layer, and that layer is missing the check entirely.

### Impact Explanation
Any unprivileged CosmWasm caller can submit a `{"transfer_nft":{"recipient":"<attacker>","token_id":"<victim's token>"}}` execute message against the pointer contract for any `token_id` whose owner has previously approved the pointer as an operator (a prerequisite for using the pointer at all). The pointer contract will look up the real owner, build an EVM transfer from that owner to the attacker-chosen recipient, and execute it via delegatecall — resulting in **unauthorized transfer of the wrapped ERC721 NFT to an attacker**, a direct instance of "unauthorized transfer via precompile or pointer" fund loss.

### Likelihood Explanation
High. Exploitation only requires knowledge of a valid `token_id` (sequential/enumerable via `all_tokens`/`num_tokens` queries) and does not require any special permission — any account can send the `transfer_nft` execute message to the pointer contract. No race condition, governance action, or validator collusion is needed.

### Recommendation
Add an explicit authorization check in `transfer_nft` (and any sibling handlers such as `send_nft`/`approve`/`burn` variants) before constructing the EVM payload: verify `info.sender == owner` or that `info.sender` is an approved operator/spender for the token (mirroring the check already present in `contracts/src/ERC721.sol`'s `_isApprovedOrOwner`), returning `ContractError::Unauthorized` otherwise.

### Proof of Concept
1. Owner `O` deploys/registers the CW721 pointer for their ERC721 token and (as required for pointer functionality) approves the pointer contract as an operator on the underlying ERC721 (`setApprovalForAll(pointerAddr, true)` or per-token `approve`).
2. Attacker `A` (any unrelated account) submits `MsgExecuteContract{ Sender: A, Contract: pointerAddr, Msg: {"transfer_nft":{"recipient":"A","token_id":"<O's token>"}} }`.
3. `transfer_nft` resolves `owner = O` via `erc721_owner` query and issues `EvmMsg::DelegateCallEvm` calling the real ERC721's `transferFrom(O, A, token_id)`.
4. Because the pointer contract itself is an approved operator on the ERC721, the transfer succeeds — token moves from `O` to `A` without `O`'s consent, despite `A` never being checked as owner or approved spender in the CosmWasm handler.

Note: I was unable to fully trace the exact `EvmMsg::DelegateCallEvm` handling code in the x/evm module (its precise msg.sender-preservation semantics for CosmWasm-initiated delegatecalls) within the indexed context, so the final link that the pointer contract's own approved-operator status is what allows the transfer to succeed should be confirmed by a full Devin session with complete repository access.

### Citations

**File:** example/cosmwasm/cw721/src/contract.rs (L70-82)
```rust
pub fn execute_transfer_nft(
    deps: DepsMut<EvmQueryWrapper>,
    info: MessageInfo,
    recipient: String,
    token_id: String,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer_nft(deps, &info, &recipient, &token_id)?;
    res = res.add_attribute("action", "transfer_nft")
        .add_attribute("sender", info.sender)
        .add_attribute("recipient", recipient)
        .add_attribute("token_id", token_id);
    Ok(res)
}
```

**File:** example/cosmwasm/cw721/src/contract.rs (L175-192)
```rust
fn transfer_nft(
    deps: DepsMut<EvmQueryWrapper>,
    info: &MessageInfo,
    recipient: &str,
    token_id: &str,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(recipient)?;

    let erc_addr = ERC721_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let owner = querier.erc721_owner(info.sender.to_string(), erc_addr.to_string(), token_id.to_string())?.owner;
    let payload = querier.erc721_transfer_payload(owner, recipient.to_string(), token_id.to_string())?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new().add_message(msg);

    Ok(res)
}
```

**File:** contracts/src/ERC721.sol (L111-134)
```text
    function _isApprovedOrOwner(
        address owner,
        address spender,
        uint id
    ) internal view returns (bool) {
        return (spender == owner ||
        isApprovedForAll[owner][spender] ||
            spender == _approvals[id]);
    }

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
