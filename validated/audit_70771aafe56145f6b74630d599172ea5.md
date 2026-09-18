Based on my investigation, I found a directly analogous instance of this bug class in sei-chain's CosmWasm CW20-to-ERC20 pointer contract, which wraps arbitrary (attacker-supplied) ERC20 tokens and is reachable by any CosmWasm user through a public `transfer`/`transfer_from` execute message.

### Title
CW20 pointer for arbitrary ERC20 tokens does not check the ERC20 `transfer`/`transferFrom` boolean return value, allowing silent transfer failures - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CW20-to-ERC20 pointer contract's `transfer` and `transfer_from` handlers build raw ERC20 calldata via `erc20_transfer_payload`/`erc20_transfer_from_payload` and dispatch it with `EvmMsg::DelegateCallEvm`, treating the call as successful as long as the EVM call itself does not revert. [1](#0-0) [2](#0-1)  Neither function inspects the `bool` return value that ERC20 `transfer`/`transferFrom` are supposed to return, which is exactly the bug class described in the external report (non-compliant/failing tokens that return `false` instead of reverting).

### Finding Description
`transfer()` fetches the pointed-to ERC20 address, computes the ABI-encoded `transfer(recipient, amount)` payload via the EVM querier, and wraps it in a `DelegateCallEvm` message without decoding or checking the return data: [3](#0-2) . The same pattern is used in `transfer_from` with `erc20_transfer_from_payload`: [4](#0-3) . If the underlying ERC20 contract that the pointer wraps is a non-standard token that returns `false` on failure (e.g., insufficient balance, blocklist, paused state) rather than reverting — analogous to `ZRX`-style tokens referenced in the report — the delegatecall itself succeeds, so the CW20 message returns success attributes (`action`, `from`, `to`, `amount`) even though no tokens were actually moved.

### Impact Explanation
Because the CW20 pointer is meant to make an arbitrary ERC20 token appear as a CW20 token to CosmWasm contracts and users, any caller/integrator (DEX, escrow, custodial CW20 logic) relying on the pointer's execute response or resulting state to confirm a transfer succeeded can be misled into believing funds moved when they did not. This can lead to fund loss for counter-parties who release CW20/other assets on the assumption that a corresponding ERC20 pointer transfer completed, satisfying the "unauthorized transfer via precompile or pointer" / fund-loss impact category.

### Likelihood Explanation
Likelihood depends on whether the underlying ERC20 token being pointed to is non-standard (returns `false` rather than reverting) — a real, moderately common quirk among ERC20 implementations (e.g., legacy `ZRX`). Any user can register a CW20 pointer for an arbitrary ERC20 contract and then invoke `transfer`/`transfer_from` on it, so the path is reachable by any public transaction sender without special privileges; the main gating factor is deployment/use of a non-compliant token, which is outside the protocol's control but foreseeable given pointers are explicitly designed to wrap arbitrary tokens.

### Recommendation
Decode the return data from the `DelegateCallEvm` result in both `transfer` and `transfer_from` (and any other value-moving pointer operations) and require it to equal Solidity `true` (or accept empty return data only, mirroring OpenZeppelin's `SafeERC20.safeTransfer` semantics), reverting the CosmWasm message if the boolean is `false`.

### Proof of Concept
1. Deploy a "non-standard" ERC20 token contract whose `transfer`/`transferFrom` functions return `false` on failure instead of reverting (mirroring `ZRX`).
2. Register a CW20 pointer for this token (produces a pointer contract backed by `example/cosmwasm/cw20`'s compiled artifact logic).
3. From an account with insufficient balance or otherwise expected to fail (e.g., blocklisted), call the CW20 pointer's `transfer` execute message.
4. Observe that `DelegateCallEvm` succeeds (no revert) because the token returns `false` rather than reverting; the CW20 execute message returns success attributes (`from`, `to`, `amount`) despite no balance change on the underlying ERC20 contract, confirming the silent-failure condition described in `transfer`/`transfer_from` at `example/cosmwasm/cw20/src/contract.rs` lines 227-274. [5](#0-4) 

**Note on verification limits:** I was unable to fully trace the Go-side handler for `EvmMsg::DelegateCallEvm` (`wasmbinding/encoder.go`, `x/evm/client/wasm/encoder.go`) within the available tool budget to confirm with 100% certainty that it does not itself perform boolean-return validation before returning success to the CosmWasm message. If that handler already decodes and enforces the boolean return value, this finding would not apply. I recommend a follow-up review of `wasmbinding/encoder.go` and `x/evm/client/wasm/encoder.go` to confirm the exact behavior of `DelegateCallEvm` message execution before treating this as fully confirmed.

### Citations

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
