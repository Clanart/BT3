### Title
Unchecked ERC20 `transfer`/`transferFrom` return value in the ERC20→CW20 wrapper contract can spoof `Cw20ReceiveMsg` deposits - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CosmWasm CW20 wrapper contract that proxies calls to an underlying ERC20 token via the EVM bridge builds a `DelegateCallEvm` message for `transfer`/`transferFrom` but never checks the ABI-encoded `bool` return value of the ERC20 call — it only relies on the low-level EVM call not reverting. In `send`/`send_from`, this unchecked transfer is followed unconditionally by a `Cw20ReceiveMsg` notification to the recipient contract, claiming that `amount` tokens were received.

### Finding Description
`transfer()` and `transfer_from()` build an `EvmMsg::DelegateCallEvm` payload for the ERC20 `transfer`/`transferFrom` selector and simply `add_message` it, without inspecting the encoded boolean return data of the call: [1](#0-0) [2](#0-1) 

`execute_send` and `execute_send_from` call this same `transfer`/`transfer_from` helper and then unconditionally append a `Cw20ReceiveMsg` to the recipient contract, asserting that `amount` was transferred: [3](#0-2) [4](#0-3) 

Cosmos SDK/CosmWasm submessages only fail (and roll back the whole tx) if the underlying EVM `delegatecall` reverts. Standard ERC-20 implementations revert on failure, but the ERC-20 standard does not require this — many real-world tokens return `false` from `transfer`/`transferFrom` on failure instead of reverting. Any user can wrap an arbitrary ERC20 token as an EVM→CW20 pointer, including such non-reverting tokens, since pointer creation is a public/unprivileged action per the pointer contract mechanism described in the module's own documentation. [5](#0-4) 

### Impact Explanation
When a malicious or non-standard ERC20 token that returns `false` (instead of reverting) on a failed transfer is wrapped, calling `send`/`send_from` on the CW20 wrapper lets an attacker with insufficient ERC20 balance or allowance still send a full-fidelity `Cw20ReceiveMsg{amount}` to a victim CosmWasm contract, since the failed underlying transfer does not abort the wasm message. Any receiving contract that trusts this message body (e.g., a lending/AMM/vault contract crediting a deposit based on `Cw20ReceiveMsg.amount`) can be tricked into crediting funds that were never actually transferred — this is a concrete unauthorized-value-creation / fund-loss vector via the CW↔EVM pointer bridge, matching the impact categories of "unauthorized transfer via precompile or pointer" / "fund loss."

### Likelihood Explanation
Likelihood is Medium: it requires (1) a pointer/wrapper being created for a non-standard ERC20 that returns `false` rather than reverting on failure — a well-documented and non-trivial-but-real class of ERC20 implementations — and (2) a downstream CW contract that trusts `Cw20ReceiveMsg.amount` without independently verifying balances. Both conditions are plausible given pointer creation is unprivileged and open to any ERC20 contract, including ones an attacker deploys themselves specifically to exploit this pattern.

### Recommendation
Decode and check the boolean return value of the ERC20 `transfer`/`transferFrom` call before treating the transfer as successful — e.g., inspect the sub-message reply data (using `SubMsg` with `ReplyOn::Success` and a corresponding `reply` entrypoint) and abort/return an error if the ERC20 call returned `false`, mirroring the SafeERC20 pattern used elsewhere in the codebase (e.g., `TransferHelper.sol`'s `require(success && (data.length == 0 || abi.decode(data, (bool))))`) before emitting `Cw20ReceiveMsg` or any other message that asserts a successful transfer occurred.

### Proof of Concept
1. Deploy a malicious ERC20 contract whose `transfer`/`transferFrom` returns `false` (no revert) when the caller lacks sufficient balance/allowance.
2. Create an ERC20→CW20 pointer/wrapper for this token (public/unprivileged action).
3. Attacker (with zero balance/allowance on the underlying ERC20) calls `send`/`send_from` on the CW20 wrapper targeting a victim contract (e.g., a vault contract) with a large `amount`.
4. `transfer`/`transfer_from` dispatches the `DelegateCallEvm` sub-message; the ERC20 call returns `false` but does not revert, so the wasm message succeeds. [2](#0-1) 
5. The wrapper still appends a `Cw20ReceiveMsg{sender, amount, msg}` to the victim contract. [4](#0-3) 
6. The victim contract credits the attacker `amount` of value despite never having received any ERC20 tokens.

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

**File:** example/cosmwasm/cw20/src/contract.rs (L227-248)
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
```

**File:** example/cosmwasm/cw20/src/contract.rs (L250-274)
```rust
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

**File:** x/evm/AGENTS.md (L102-117)
```markdown
## Pointer Contracts (CW ↔ ERC Interoperability)

Pointer contracts enable tokens on one VM to be accessed from the other VM. This is the primary interoperability mechanism between CosmWasm and EVM.

**EVM pointers for CW/native tokens:**
- Native Cosmos denoms get an ERC20 representation.
- CW20 tokens get an ERC20 pointer.
- CW721 NFTs get an ERC721 pointer.
- CW1155 multi-tokens get an ERC1155 pointer.

**CW wrappers for ERC tokens:**
- ERC20 tokens get a CW20 wrapper.
- ERC721 NFTs get a CW721 wrapper.
- ERC1155 multi-tokens get a CW1155 wrapper.

Pointers are versioned and can be upgraded. A reverse registry allows looking up the original token from its pointer address.
```
