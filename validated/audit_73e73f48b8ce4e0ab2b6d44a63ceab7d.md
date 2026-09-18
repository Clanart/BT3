Found a concrete analog. This is the reference `example/cosmwasm/cw721/src/contract.rs` implementation of a CW721-to-ERC721 wrapper contract (the paired analog of the CW721↔ERC721 pointer path). It relies on `transfer_nft`/`execute_approve` to authorize an EVM-side transfer/approval, but the authorization decision is derived by querying an *unrelated* piece of state (`erc721_owner` keyed on `info.sender`) instead of verifying the caller's actual permission (ownership or approval) over the specific token being transferred/approved — mirroring the Kimai pattern of trusting a loosely-related historical/derived value instead of re-checking the current permission on the object actually being acted upon.

### Title
Missing per-token ownership/approval check in CW721↔ERC721 pointer wrapper `transfer_nft`/`execute_approve` allows unauthorized ERC721 transfers and approvals - ([File: example/cosmwasm/cw721/src/contract.rs])

### Summary
The `transfer_nft` helper (used by both `TransferNft` and `SendNft` execute messages) and `execute_approve` in the CW721→ERC721 pointer/wrapper contract never verify that `info.sender` is the actual owner of, or is approved for, `token_id` before issuing a `DelegateCallEvm` that invokes the underlying ERC721's `transferFrom`/`approve` on behalf of the wrapper contract.

### Finding Description
`transfer_nft` builds the ERC721 transfer payload from an `owner` value obtained by calling `querier.erc721_owner(info.sender.to_string(), erc_addr.to_string(), token_id.to_string())?.owner` [1](#0-0) . That RPC method name suggests an ownership query, but nothing in this function asserts that `info.sender` actually equals the returned owner (or is an approved operator) for `token_id` — it simply forwards whatever `owner` value comes back into the `erc721_transfer_payload`, which is then executed as a `delegatecall` into the real ERC721 contract from the wrapper's own address [2](#0-1) . Because the call is a `DelegateCallEvm` (execution context is the pointer/wrapper contract, not `info.sender`), the actual `msg.sender` seen by the ERC721 contract is the wrapper address, and the wrapper's own logic — not the ERC721's `require(from == owner ...)` check performed inside the pointer — is the only gate standing between an arbitrary caller and moving someone else's token. Since `transfer_nft` does not compare `info.sender` to the queried `owner`/approved-spender, any CosmWasm caller can invoke `TransferNft`/`SendNft` supplying an arbitrary `token_id`, and the resulting delegatecall will attempt to move that token using whatever "owner" the query happens to resolve.

Similarly, `execute_approve` builds and dispatches an `approve`/`revoke` delegatecall for `token_id` on behalf of the wrapper without ever checking that `info.sender` is the current owner of `token_id` (or already approved) [3](#0-2) . It just forwards `spender` and `token_id` straight into the ERC721 `approve` payload signed as the wrapper contract.

This is directly analogous to the Kimai vulnerability: the CWE-285/862 root cause in both cases is that an authorization decision for a *new* action on a resource (spend/approve rights on `project`+`activity`, vs. transfer/approve rights on an NFT `token_id`) is derived from a loosely-coupled reference (the historical timesheet's stored project fields, vs. an unchecked ownership query result) rather than a fresh, explicit comparison of "does the caller currently have authority over exactly this object." In Kimai the authorization was granted based on *possession* of an old record; here it's effectively granted based on *possession of a valid-looking query response* rather than an explicit `sender == owner || sender == approved` gate.

### Impact Explanation
If the underlying ERC721 does not itself perform an additional authorization check identical to the classic `require(msg.sender == owner || isApprovedForAll[owner][msg.sender] || msg.sender == getApproved(tokenId))` (as seen in the reference `ERC721.sol`/pointer contracts) that is evaluated against the wrapper's true caller context, an attacker could route an unauthorized `transferFrom`/`approve` for someone else's NFT through the wrapper, resulting in unauthorized transfer of an asset (fund/asset loss) via a CW-to-EVM pointer path reachable by any CW message sender — squarely inside the in-scope "CW<->EVM pointers and the wasm bridge" surface.

### Likelihood Explanation
Medium-to-High: the code path is reachable by any unprivileged CosmWasm user who can execute a message against this contract (`TransferNft`, `SendNft`, `Approve`); no special privilege is required to trigger the missing check, and the flaw is a straightforward omission of an ownership/approval assertion rather than a subtle race condition.

### Recommendation
Before constructing the ERC721 transfer/approve delegatecall payload, explicitly verify current authorization on the specific `token_id`: query the ERC721's actual `ownerOf(token_id)` and `getApproved(token_id)`/`isApprovedForAll(owner, info.sender)`, and reject the call (return a `ContractError::Unauthorized`) unless `info.sender` equals the owner or is an approved operator/spender for that token — mirroring the pattern the Kimai fix applied (re-derive and check current authority at the point of use, rather than trusting an indirect reference).

### Proof of Concept
Not independently reproduced against a live pointer deployment; the analysis is based on static review of `transfer_nft`/`execute_approve` in `example/cosmwasm/cw721/src/contract.rs`, which lacks any `info.sender == owner/approved` assertion prior to dispatching the EVM delegatecall. Verifying real-world exploitability would require confirming whether the deployed ERC721 pointer/target additionally enforces `msg.sender`-based authorization in a way that neutralizes this gap in the wrapper — this could not be fully confirmed from the available indexed code and would need direct execution against a running pointer instance to conclusively validate.

### Citations

**File:** example/cosmwasm/cw721/src/contract.rs (L106-132)
```rust
pub fn execute_approve(
    deps: DepsMut<EvmQueryWrapper>,
    info: MessageInfo,
    spender: String,
    token_id: String,
    approved: bool,
) -> Result<Response<EvmMsg>, ContractError> {
    let erc_addr = ERC721_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let mut payload_spender = spender.clone();
    let mut action = "approve";
    if !approved {
        payload_spender = "".to_string();
        action = "revoke";
    }
    let payload = querier.erc721_approve_payload(payload_spender, token_id.clone())?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("action", action)
        .add_attribute("token_id", token_id)
        .add_attribute("sender", info.sender)
        .add_attribute("spender", spender.clone())
        .add_message(msg);

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
