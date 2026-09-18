### Title
CW20 wrapper contract does not check ERC20 return value on `DelegateCallEvm` transfer/transferFrom - can mint/transfer CW20 balances without moving underlying ERC20 tokens - (File: example/cosmwasm/cw20/src/contract.rs)

### Summary
The example CosmWasm `cw20` contract that wraps a pointed-to EVM ERC20 token (`ERC20_ADDRESS`) implements `transfer` and `transfer_from` by issuing an `EvmMsg::DelegateCallEvm` calling the underlying ERC20 contract's `transfer`/`transferFrom` selector, but never inspects the ABI-encoded `bool` return value (or reverts) of that delegatecall before reporting success to the caller.

### Finding Description
In `transfer` and `transfer_from`, the contract builds an EVM-encoded payload via `EvmQuerier::erc20_transfer_payload` / `erc20_transfer_from_payload` and dispatches it as an `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }` submessage: [1](#0-0) [2](#0-1) 

The `Response` is built and returned unconditionally with `add_attribute`/`add_message` — there is no reply handler or subsequent check that decodes the ERC20 `transfer`/`transferFrom` boolean return value. Per ERC20's non-reverting semantics (as described in the referenced report for tokens like ZRX), a call that returns `false` on failure — rather than reverting — will still be treated by this contract as a successful transfer, because only the delegatecall's low-level revert (not its ABI return value) would cause failure. This mirrors exactly the bug class described in the external report: unchecked ERC20 `transfer`/`transferFrom` return values enabling accounting to diverge from actual token movement. This is compounded by the fact that the CW20 wrapper does not track its own balance state for the wrapped token — its `balanceOf`-style state is implicitly the ERC20 balance queried elsewhere — meaning any caller could invoke `transfer_from`/`transfer` against a non-standard/misbehaving ERC20 pointee and have the contract emit success attributes/events while the underlying token balance failed to move.

Compare this to the peer pointer contract `CW20ERC20Pointer.sol` (opposite direction, ERC20 wrapping CW20) which does check the delegatecall's `bool success` from the wasmd precompile with `require(success, "CosmWasm execute failed")`: [3](#0-2) 
No equivalent check of the ERC20 ABI return value exists in the `cw20` Rust wrapper's `transfer`/`transfer_from`.

### Impact Explanation
If the wrapped/pointed ERC20 token is non-standard (returns `false` instead of reverting on failed transfers — a documented real-world pattern, e.g. ZRX-style tokens), this contract's `transfer`/`transfer_from` will report and emit success attributes without the underlying tokens actually moving. Any downstream consumer relying on this CW20 wrapper's emitted events/attributes as proof of transfer (e.g., other contracts, off-chain indexers, or composed DeFi flows) can be tricked into crediting balances or unlocking value without real asset movement — a fund-loss/accounting-divergence bug consistent with the "supply inflation" class of impact for wrapped-token pointers.

### Likelihood Explanation
This code lives in `example/cosmwasm/cw20/`, which functions as reference/example CosmWasm contract code for wrapping ERC20 pointees. If deployed as-is (or used as a template for a production CW20 pointer wrapping an EVM ERC20), any user able to instantiate this contract pointing at an attacker-controlled or noncompliant ERC20 and then invoke `transfer`/`transfer_from` via a wasm message can trigger the divergence — reachable from a standard CosmWasm execute message, no special privilege required. Likelihood of exploitation depends on whether this example contract is used as-is in production versus purely illustrative code (uncertain, not confirmed from available context) — this is a caveat on real-world severity.

### Recommendation
After returning from the `DelegateCallEvm` submessage, add a `Reply` handler that decodes the ABI-encoded return data of the ERC20 `transfer`/`transferFrom` call and asserts it equals `true` (reverting the CW20 message otherwise), mirroring the `require(success, ...)` pattern used in `CW20ERC20Pointer.sol`. Alternatively, use SubMsg with `ReplyOn::Always` and validate the raw call succeeded and returned `true`, treating empty/false returns as failures.

### Proof of Concept
1. Deploy the example `cw20` contract configured with `ERC20_ADDRESS` pointing to a malicious/non-standard ERC20 contract whose `transfer`/`transferFrom` returns `false` (ABI-encoded) instead of reverting on failure (e.g., when the caller has insufficient balance).
2. Call the CW20 contract's `transfer` or `transfer_from` execute message.
3. `EvmQuerier::erc20_transfer_payload`/`erc20_transfer_from_payload` builds the call; `EvmMsg::DelegateCallEvm` executes it. The delegatecall itself succeeds (no revert) but returns `abi.encode(false)`.
4. The contract's `transfer`/`transfer_from` handler does not inspect this return value; it unconditionally returns `Response::new()` with success attributes and the message dispatched, giving the appearance the transfer succeeded even though the underlying ERC20 balance did not move. [4](#0-3)

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

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```
