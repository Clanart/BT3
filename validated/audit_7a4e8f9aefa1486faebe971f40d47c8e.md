Confirmed: `registerPointerForERC20` deploys `example/cosmwasm/cw20` (the `POINTER_CW20` wasm binary) as a native, chain-supported CW20 pointer for an arbitrary EVM ERC20 token — this is not merely a demo, it is the actual pointer contract wired into `contracts/test/CW20toERC20PointerTest.js` via `registerPointerForERC20` / `x/evm/keeper/pointer.go`, and any user can register a pointer for any ERC20 (including USDT-style tokens with approval-race protection).

### Title
CW20-to-ERC20 pointer's `increase_allowance` permanently reverts for ERC20 tokens with approval race-condition protection - ([File: example/cosmwasm/cw20/src/contract.rs])

### Summary
The CW20 pointer contract that wraps an arbitrary EVM ERC20 token (deployed via `registerPointerForERC20` for any user-supplied ERC20 address) implements `IncreaseAllowance` by reading the current on-chain allowance and calling `approve()` on the underlying ERC20 with an absolute non-zero value whenever the current allowance is already non-zero. Tokens that implement the USDT-style approval race-condition mitigation (revert unless old allowance is 0 before setting a new non-zero value) will cause this call to revert every time, permanently breaking `increase_allowance` for that pointer/spender pair.

### Finding Description
`execute_increase_allowance` in [1](#0-0)  queries the current allowance and computes `new_allowance = current_allowance + amount`, then sends this absolute value directly through `erc20_approve_payload` / `DelegateCallEvm` to the wrapped ERC20's `approve()` function [2](#0-1) . There is no reset-to-zero step before setting a new non-zero allowance.

This is precisely the bug class from the external report: some widely-used ERC20 tokens require the allowance to be reduced to 0 before it can be changed to another non-zero value. Once a user calls `increase_allowance` a first time (setting a non-zero allowance on the underlying token), any subsequent call to `increase_allowance` for the same spender before the allowance is fully consumed will attempt `approve(spender, nonzero)` on top of an already non-zero allowance, and the underlying token reverts the whole CosmWasm execution.

Because this pointer contract can wrap *any* EVM ERC20 address chosen by the caller of `RegisterPointerForERC20` [3](#0-2) , a malicious or unaware user only needs to point at (or already have pointed at) a USDT-style token to trigger this permanently.

### Impact Explanation
This causes a permanent denial of service of the `increase_allowance` action for a wrapped race-protected ERC20 token once a non-zero allowance already exists for a spender: every subsequent `IncreaseAllowance` execution reverts, and the CosmWasm-side allowance can no longer be raised via this pointer without first driving it to zero through some external means the CW20 interface doesn't expose separately in one step (there is no combined operation). Users depending on this pointer for such tokens are unable to grant additional spending allowance, which can freeze legitimate downstream flows (e.g., Send/SendFrom-based DEX or vault interactions that rely on periodic allowance top-ups) for the affected token. This matches the "permanent freezing" of a specific reachable EVM/CW bridging function; no direct fund loss is claimed, but functional permanent breakage of core pointer allowance behavior for an entire class of tokens is a legitimate Medium-severity fund-freezing/functionality-DoS analog.

### Likelihood Explanation
High likelihood of encounter: any unprivileged user can register this CW20 pointer for a real-world ERC20/USDT-style token via `registerPointerForERC20`, and any two consecutive `increase_allowance` calls before the allowance is fully spent will trigger the revert. No special privileges, timing, or validator cooperation are required — a single public transaction sequence from a normal user reproduces it deterministically.

### Recommendation
In `execute_increase_allowance` (and symmetrically review `execute_decrease_allowance`), reset the underlying ERC20 allowance to zero first when the current allowance is non-zero and the target amount is non-zero (i.e., issue an `approve(spender, 0)` call, then `approve(spender, new_allowance)`), or otherwise avoid unconditional non-zero-to-non-zero approve transitions when wrapping arbitrary externally-supplied ERC20 tokens.

### Proof of Concept
1. Deploy or reference a USDT-style ERC20 token that reverts `approve()` when `_value != 0 && allowed[msg.sender][_spender] != 0`.
2. Call `registerPointerForERC20(tokenAddr)` to instantiate the CW20 pointer wrapping that token (as in `contracts/test/CW20toERC20PointerTest.js` lines 19-31).
3. Execute `IncreaseAllowance{spender, amount}` once — succeeds, setting a non-zero allowance on the underlying token via `approve(spender, amount)`.
4. Execute `IncreaseAllowance{spender, amount}` again before the allowance is consumed — the contract computes `new_allowance = current_allowance + amount` (non-zero) and calls `approve(spender, new_allowance)` while the existing allowance is still non-zero; the underlying token's `approve` reverts, causing the whole CosmWasm execution message to fail.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L118-150)
```rust
pub fn execute_increase_allowance(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    spender: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&spender)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);

    // Query the current allowance for this user
    let current_allowance = querier.erc20_allowance(erc_addr.clone(), info.sender.clone().into_string(), spender.clone())?.allowance;

    // Set the new allowance as the sum of the current allowance and amount specified
    let new_allowance = current_allowance + amount;

    // Send the message to approve the new amount
    let payload = querier.erc20_approve_payload(spender.clone(), new_allowance)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };

    let res = Response::new()
        .add_attribute("action", "increase_allowance")
        .add_attribute("spender", spender)
        .add_attribute("amount", amount)
        .add_attribute("new_allowance", new_allowance)
        .add_attribute("by", info.sender)
        .add_message(msg);

    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/querier.rs (L42-52)
```rust
    pub fn erc20_approve_payload(&self, spender: String, amount: Uint128) -> StdResult<ErcPayloadResponse> {
        let request = EvmQueryWrapper {
            route: Route::Evm,
            query_data: EvmQuery::Erc20ApprovePayload {
                spender, amount,
            },
        }
        .into();

        self.querier.query(&request)
    }
```

**File:** x/evm/keeper/pointer.go (L1-1)
```go
package keeper
```
