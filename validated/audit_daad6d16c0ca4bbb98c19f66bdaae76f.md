### Title
`example/cosmwasm/cw20` ERC20-wrapping CW20 contract does not verify the boolean success return of the wrapped ERC20's `transfer`/`transferFrom` calls - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The Sei CW↔EVM bridge documents a "CW wrapper for ERC tokens" pattern where an arbitrary EVM ERC20 token can be wrapped so it is usable as a CW20 from CosmWasm [1](#0-0) . The reference implementation of this wrapper, `example/cosmwasm/cw20`, builds the ERC20 `transfer`/`transferFrom` calldata and dispatches it as an `EvmMsg::DelegateCallEvm`, but never inspects or validates the ABI-encoded `bool` return value of the underlying ERC20 call — it only relies on whatever `Response` is produced by the message, and unconditionally emits success attributes.

### Finding Description
The `transfer` and `transfer_from` helper functions build the calldata via `EvmQuerier::erc20_transfer_payload` / `erc20_transfer_from_payload` and wrap it in a `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }` message, then immediately construct a `Response` with `from`/`to`/`amount` attributes and `add_message(msg)` [2](#0-1) . Neither `transfer` nor `transfer_from` decodes the `bool` returned by the ERC20 `transfer`/`transferFrom` selector to confirm the underlying token actually recorded the transfer — the code assumes any wrapped ERC20 either reverts on failure or always transfers successfully.

This is the exact bug class from the Cally report: some ERC20 tokens (e.g. ZRX-style tokens) do not revert on a failed transfer, they simply return `false`. If such a non-compliant ERC20 is wrapped by this CW20 pointer contract, `execute_transfer`/`execute_transfer_from` will complete "successfully" from the CosmWasm module's perspective (the EVM call itself did not revert), emit `Transfer`-style success attributes, and the calling contract logic (e.g. a `Send`/`SendFrom` flow that also fires a `Cw20ReceiveMsg` to a receiving contract) will proceed to treat the transfer as done, even though the wrapped ERC20 balance was never actually moved.

### Impact Explanation
Any CosmWasm caller can invoke `Transfer`/`TransferFrom`/`Send`/`SendFrom` on a CW20 wrapper deployed over a non-reverting/non-standard ERC20. Downstream contracts (or the wrapper's own allowance/balance bookkeeping, if any is layered on top by an integrator) can be tricked into believing a transfer succeeded when it did not, enabling unauthorized value extraction analogous to the Cally optioned-vault drain: a counterparty can receive CW-side attribution/consideration (fees, allowances consumed, downstream `Send` callbacks executed) for a token transfer that never happened on the EVM side. This falls under "unauthorized transfer via precompile or pointer" / fund-loss impact for CW20-pointer users interacting with such wrapped tokens.

### Likelihood Explanation
Reachable by any CosmWasm user who deploys or interacts with this documented ERC20-to-CW20 wrapper pattern and picks (or is tricked into using) a non-standard, non-reverting ERC20 token as the wrapped asset — no privileged access is required, matching the "CosmWasm user" and "CW<->EVM pointers" reachable categories. The main uncertainty is that this code lives under `example/cosmwasm/cw20`, i.e., it is shipped as a reference/example contract rather than as an embedded, chain-enforced pointer artifact (unlike `CW20ERC20Pointer.sol`, which is compiled into `x/evm/artifacts` and does `require(success, "CosmWasm execute failed")` on every wasmd `_execute` call). I could not fully confirm from the available index whether `EvmMsg::DelegateCallEvm`'s handler (in the EVM module's wasm bindings) itself decodes and enforces the ABI `bool` return value before considering the CosmWasm message successful — that would need direct inspection of the Go-side wasm binding handler for `DelegateCallEvm`, which was not available in the indexed results.

### Recommendation
In `transfer`/`transfer_from` (and any other function forwarding value-moving ERC20 calls), decode the ABI-encoded boolean return of the delegated EVM call and return `ContractError` if it is `false`, rather than assuming success whenever the low-level call does not revert. Equivalently, enforce this check at the `EvmMsg::DelegateCallEvm` dispatch layer so that any wrapper contract using it automatically gets return-value validation for standard ERC20 selectors.

### Proof of Concept
Not independently reproducible from the indexed context alone: a full PoC would require deploying `example/cosmwasm/cw20` pointing at a non-reverting ERC20 (mirroring the ZRX-style token from the original report), calling `ExecuteMsg::TransferFrom` with an amount that the ERC20's `transferFrom` silently rejects, and observing that the CW20 wrapper's `execute_transfer_from` still returns a successful `Response` with `action=transfer_from` attributes despite no on-chain balance change on the EVM side — analogous to the `testStealFunds` PoC in the original report.

### Citations

**File:** x/evm/AGENTS.md (L112-115)
```markdown
**CW wrappers for ERC tokens:**
- ERC20 tokens get a CW20 wrapper.
- ERC721 NFTs get a CW721 wrapper.
- ERC1155 multi-tokens get a CW1155 wrapper.
```

**File:** example/cosmwasm/cw20/src/contract.rs (L227-274)
```rust
fn transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_payload(recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", info.sender)
        .add_attribute("to", recipient)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}

pub fn transfer_from(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    owner: String,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_from_payload(owner.clone(), recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", owner)
        .add_attribute("to", recipient)
        .add_attribute("by", info.sender)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```
