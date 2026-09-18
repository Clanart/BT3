### Title
CW20-for-ERC20 pointer contract does not check the ERC20 `transfer`/`transferFrom`/`approve` boolean return value before crediting/notifying recipients - (File: example/cosmwasm/cw20/src/contract.rs)

### Summary
The Sei "cw-erc20" wrapper contract (the CosmWasm contract deployed as a CW20 pointer that wraps an ERC20 token, compiled into `x/evm/artifacts/erc20/cwerc20.wasm`) issues `transfer`, `transferFrom`, and `approve` calls to the wrapped ERC20 via `EvmMsg::DelegateCallEvm` but never inspects the ABI-encoded `bool` return value of those calls. It only relies on the CosmWasm message succeeding (i.e., the EVM call not reverting), exactly the anti-pattern flagged in the referenced Footium `FootiumEscrow.sol` report.

### Finding Description
`transfer`, `transfer_from`, `execute_increase_allowance`, and `execute_decrease_allowance` in [1](#0-0)  and [2](#0-1)  build an ABI payload for the ERC20's `transfer`/`transferFrom`/`approve` functions and wrap it in a `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }` submessage, then immediately construct a "successful" `Response` with `add_message(msg)` — without decoding or checking the returned `bool` from the ERC20 call.

`execute_send` and `execute_send_from` compound this: they call the internal `transfer`/`transfer_from` helper (whose success is only "did not revert") and then unconditionally attach a `Cw20ReceiveMsg` notification to the destination contract, informing it that the transfer succeeded: [3](#0-2)  and [4](#0-3) .

Any ERC20-compliant token that does not revert on failed transfers (e.g. tokens like older USDT-style tokens that return `false` instead of reverting, or a malicious pointer target) will cause `DelegateCallEvm` to succeed at the EVM level while the underlying `transfer`/`transferFrom`/`approve` silently does nothing. Since the wrapper contract has no independent ledger — reads (`query_balance`, `query_allowance`) proxy directly to the ERC20 (`querier.erc20_balance`, `querier.erc20_allowance` per [5](#0-4) ) — a plain `transfer`/`transfer_from` call alone would just be a no-op with no state divergence. However, `send`/`send_from` fire a follow-up `Cw20ReceiveMsg` to a third-party CW contract regardless of whether the ERC20 transfer actually moved funds, so any downstream contract (e.g. a DEX or vault) that credits the sender or releases assets purely because it received the `Cw20ReceiveMsg` callback can be tricked into releasing funds without ever receiving the underlying ERC20 tokens.

### Impact Explanation
This can lead to concrete fund loss: a downstream CosmWasm contract that trusts the `Cw20ReceiveMsg` notification from the ERC20-wrapping CW20 pointer (as is standard CW20 "receiver" pattern for deposits/swaps) can be made to credit or release funds for a transfer that never actually happened, if the underlying pointed-to ERC20 is a non-reverting, false-returning token.

### Likelihood Explanation
Exploitability requires the wrapped ERC20 to be a non-standard token that returns `false` on failure instead of reverting (a known but not uncommon real-world pattern, e.g. legacy USDT-style tokens), and it requires a downstream integrator to trust the `Cw20ReceiveMsg` callback without independently verifying the balance change. Both preconditions are plausible for permissionless pointer creation (any user can create an ERC20→CW20 pointer for an arbitrary ERC20 contract), making this a realistic medium-likelihood issue rather than purely theoretical.

### Recommendation
Decode the ABI-encoded `bool` return value from the `DelegateCallEvm` reply (via a `SubMsg` with `reply_on_success`/checking `Reply.result.unwrap_data()`válue) before treating the ERC20 `transfer`/`transferFrom`/`approve` as successful, and abort/`Err` out (or refrain from emitting the `Cw20ReceiveMsg`) if the returned boolean is `false`. Alternatively, only support pointers for known ERC20-compliant tokens that revert on failure, and explicitly document/enforce this restriction at pointer-registration time.

### Proof of Concept
1. Deploy a minimal ERC20-like contract whose `transfer`/`transferFrom` functions return `false` on failure (e.g., insufficient balance) instead of reverting.
2. Register a CW20 pointer for this ERC20 via the standard pointer registration flow (`AddERC20Pointer`/`registerPointerForERC20`, as exercised in [6](#0-5) ), which deploys the wasm contract in [1](#0-0) .
3. Call `send`/`send_from` on the CW20 pointer with an amount exceeding the sender's actual ERC20 balance so the ERC20's `transfer` call returns `false` without reverting.
4. Observe that the CosmWasm `execute` still returns a successful `Response` and dispatches `Cw20ReceiveMsg` to the target contract, which believes it received funds it never got — this is unverifiable/unconfirmable from the index alone since the reply-handling for `DelegateCallEvm` in the wasm bindings/keeper could not be fully traced in this session; a Devin session with full repo access should verify whether any reply-side check exists in the `x/evm` wasmbinding message handler for `DelegateCallEvm` before confirming exploitability end-to-end.

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

**File:** example/cosmwasm/cw20/src/contract.rs (L276-301)
```rust
pub fn query_allowance(deps: Deps<EvmQueryWrapper>, owner: String, spender: String) -> StdResult<Binary> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&spender)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let allowance = querier.erc20_allowance(erc_addr, owner, spender)?;
    to_json_binary(&AllowanceResponse{allowance: allowance.allowance, expires: Expiration::Never{}})
}

pub fn query_token_info(deps: Deps<EvmQueryWrapper>, env: Env) -> StdResult<Binary> {
    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let token_info = querier.erc20_token_info(erc_addr, env.clone().contract.address.into_string())?;
    to_json_binary(&token_info)
}

pub fn query_balance(deps: Deps<EvmQueryWrapper>, account: String) -> StdResult<Binary> {
    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let balance = querier.erc20_balance(erc_addr, account.clone())?;
    to_json_binary(&balance)
}
```

**File:** contracts/test/CW20toERC20PointerTest.js (L1-31)
```javascript
const {getAdmin, queryWasm, executeWasm, associateWasm, deployEvmContract, setupSigners, deployErc20PointerForCw20, deployWasm, WASM,
    registerPointerForERC20,
    proposeCW20toERC20Upgrade
} = require("./lib");
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("CW20 to ERC20 Pointer", function () {
    let accounts;
    let admin;
    let testToken;
    let cw20Pointer;

    async function setBalance(addr, balance) {
        const resp = await testToken.setBalance(addr, balance);
        await resp.wait();
    }

    before(async function () {
        accounts = await setupSigners(await hre.ethers.getSigners());

        // Deploy TestToken
        testToken = await deployEvmContract("TestToken", ["TEST", "TEST"]);
        const tokenAddr = await testToken.getAddress();

        // Give admin balance
        admin = await getAdmin();
        await setBalance(admin.evmAddress, 1000000000000);

        cw20Pointer = await registerPointerForERC20(tokenAddr);
    });
```
