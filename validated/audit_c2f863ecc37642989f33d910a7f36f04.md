### Title
Unsafe ERC20 return-value handling in the ERC20-to-CW20 pointer wrapper allows crediting fake transfers to receiving CW contracts - ([File: example/cosmwasm/cw20/src/contract.rs])

### Summary
The ERC20-to-CW20 pointer wrapper contract (the CosmWasm source used to generate the CW20 wrapper artifact for arbitrary EVM ERC20 tokens) issues `transfer`/`transferFrom` calls to the underlying wrapped ERC20 contract via `EvmMsg::DelegateCallEvm`, but never inspects the ABI-encoded `bool` return value of the ERC20 call — it only relies on the call not reverting. Any wrapped ERC20 that returns `false` instead of reverting on a failed transfer (a legal, common pattern for many real-world tokens, e.g. blacklist/pausable/fee tokens) will cause the wrapper to treat the transfer as successful.

### Finding Description
`transfer()` and `transfer_from()` in [1](#0-0)  build an `Erc20TransferPayload`/`Erc20TransferFromPayload` and dispatch it as `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }`. The `Response<EvmMsg>` is constructed and returned unconditionally with `from`/`to`/`amount` attributes and an added message — there is no subsequent check of the delegatecall's returned data (the ERC20 `bool` success flag).

This is functionally identical to the reported bug class: the contract "uses transfer and transferFrom to send funds... but is not checking the status of the transaction," so "even if the actual transfer fails for some tokens... it will be marked as valid." Here, "marked as valid" manifests as the CW message succeeding and, critically, feeding into `execute_send`/`execute_send_from` [2](#0-1)  and [3](#0-2) , which construct a `Cw20ReceiveMsg` with the claimed `amount` and dispatch it to a receiving contract (e.g., a DEX, vault, or lending pool built to accept CW20 deposits) via `cw20receive_into_cosmos_msg`. That receiving contract will credit the sender based on the claimed `amount` in the `Cw20ReceiveMsg`, believing the transfer succeeded, even though the underlying ERC20 balance never moved (because the wrapped token silently returned `false`).

The `query_balance` function [4](#0-3)  does query the real ERC20 balance rather than tracking an internal ledger, so the wrapper's own balance view is not corrupted — but any downstream contract that trusts the `Send`/`SendFrom` message's `Cw20ReceiveMsg` amount as proof-of-deposit (rather than independently re-querying the actual balance change) can be tricked into crediting value that was never actually transferred.

### Impact Explanation
An attacker who deploys or is permitted to wrap a non-standard ERC20 (one that returns `false` rather than reverting on failed transfer — e.g., due to an internal blacklist, pause, or fee-on-transfer edge case that yields zero net effect) can call `SendFrom`/`Send` on the CW20 wrapper targeting a DeFi contract that consumes `Cw20ReceiveMsg`. The receiving contract will act as though it received `amount` tokens and credit the caller accordingly (e.g., mint LP shares, register a deposit, or release a paired asset), while no real value was transferred. This can result in direct theft of funds from the receiving contract/pool, matching the "if one token is non-revert on transfer, the other token can be stolen" impact class from the analog report.

### Likelihood Explanation
Reachable by any unprivileged EVM/CosmWasm user with a submitted transaction/wasm message: create or use an existing CW20-pointer-wrapped ERC20 that exhibits non-reverting transfer failures, then call `send`/`send_from` against a target contract that trusts the `Cw20ReceiveMsg` amount. No validator, governance, or node-level compromise is required — only a wasm message from a standard CosmWasm user.

### Recommendation
After issuing the `DelegateCallEvm` for `transfer`/`transferFrom`, decode and verify the returned ABI-encoded boolean (and/or query the recipient's/owner's ERC20 balance before and after) before returning a successful `Response`, and only forward the `Cw20ReceiveMsg`/emit success attributes if the transfer is confirmed. Alternatively, adopt a "measure actual balance delta" pattern (similar to `SafeERC20`) instead of trusting the boolean return or mere non-reversion of the call.

### Proof of Concept
1. Deploy an ERC20 token whose `transfer`/`transferFrom` returns `false` (not revert) under some attacker-controllable condition (e.g., a token with a pausable/blacklist flag that silently no-ops).
2. Register/create the CW20-ERC20 pointer wrapper for this token (`example/cosmwasm/cw20` contract, instantiated with `erc20_address` = the malicious token).
3. Call `send_from` (or `send`) on the wrapper targeting a victim CosmWasm contract that implements `Cw20ReceiveMsg` and credits deposits based on the message `amount` (e.g., a CW20-based AMM pool or vault).
4. Because `transfer_from` in [5](#0-4)  never checks the delegatecall's boolean return, the wrapper message succeeds and the `Cw20ReceiveMsg` with the claimed `amount` is forwarded to the victim contract, which credits the attacker despite no real ERC20 balance change.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L95-114)
```rust
pub fn execute_send(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    contract: String,
    amount: Uint128,
    msg: Binary,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer(deps, _env, info.clone(), contract.clone(), amount)?;
    let send = Cw20ReceiveMsg {
        sender: info.sender.to_string(),
        amount: amount.clone(),
        msg,
    };

    res = res
        .add_message(cw20receive_into_cosmos_msg(contract.clone(), send)?)
        .add_attribute("action", "send");
    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L205-225)
```rust
pub fn execute_send_from(
    deps: DepsMut<EvmQueryWrapper>,
    env: Env,
    info: MessageInfo,
    owner: String,
    contract: String,
    amount: Uint128,
    msg: Binary,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer_from(deps, env, info.clone(), owner, contract.clone(), amount)?;
    let send = Cw20ReceiveMsg {
        sender: info.sender.to_string(),
        amount: amount.clone(),
        msg,
    };

    res = res
        .add_message(cw20receive_into_cosmos_msg(contract.clone(), send)?)
        .add_attribute("action", "send_from");
    Ok(res)
}
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

**File:** example/cosmwasm/cw20/src/contract.rs (L295-301)
```rust
pub fn query_balance(deps: Deps<EvmQueryWrapper>, account: String) -> StdResult<Binary> {
    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let balance = querier.erc20_balance(erc_addr, account.clone())?;
    to_json_binary(&balance)
}
```
