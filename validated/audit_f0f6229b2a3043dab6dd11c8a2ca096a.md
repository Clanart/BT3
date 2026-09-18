### Title
CW20-over-ERC20 pointer's `increase_allowance`/`decrease_allowance` uses a single-step `approve()` set-value call to the underlying ERC20, which can permanently brick allowance updates for USDT-style tokens - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CosmWasm CW20 contract that wraps an arbitrary underlying ERC20 (used as the CW20-over-ERC20 pointer, exercised by `contracts/test/CW20toERC20PointerTest.js`) implements `execute_increase_allowance` and `execute_decrease_allowance` by computing a new absolute allowance value and calling the underlying ERC20's `approve()` once with that value, rather than performing a zero-then-set two-step approval or a native increase/decrease call.

### Finding Description
`execute_increase_allowance` and `execute_decrease_allowance` query the current allowance on the underlying ERC20 via `querier.erc20_allowance`, compute `new_allowance = current_allowance +/- amount`, and then submit a single `erc20_approve_payload(spender, new_allowance)` call directly to the underlying ERC20 contract: [1](#0-0) [2](#0-1) 

This is the same "one-step approval on an arbitrary underlying" pattern flagged in the referenced Alchemix finding: it calls `approve()` going from a non-zero allowance directly to another non-zero allowance in a single transaction. Tokens implementing the well-known "approval race protection" (e.g., USDT-style ERC20s, which explicitly disallow changing a non-zero allowance to another non-zero value without first resetting to zero) will cause this `approve()` call to revert whenever the CW20 pointer's currently-tracked allowance for a spender on the underlying ERC20 is already non-zero.

By contrast, the Solidity ERC20-over-CW20 pointer (`contracts/src/CW20ERC20Pointer.sol`) is not vulnerable because CW20's native message set only has `increase_allowance`/`decrease_allowance` semantics on the CW20 side, which are delta-based by design (no raw "set absolute value" approve call to the underlying CW20 that could clash with a race-protection allowance check): [3](#0-2) 

### Impact Explanation
Once a spender's allowance on the underlying ERC20 (as tracked through the CW20 pointer) becomes non-zero, any subsequent `increase_allowance` or `decrease_allowance` call that does not first drive the allowance to zero will revert if the underlying ERC20 enforces approval race protection. This permanently blocks legitimate allowance management (and by extension `transfer_from`-based flows depending on that allowance) for that spender through the pointer, denying availability of core CW20 functionality whenever the underlying pointed-to asset is a token like USDT that forbids non-zero-to-non-zero `approve()` transitions.

### Likelihood Explanation
Likelihood depends on whether this CW20 pointer contract is deployed for underlying ERC20s that use approval race protection. Given `contracts/test/CW20toERC20PointerTest.js` exercises this exact contract/flow, and Sei's pointer system is designed to let users create pointers for arbitrary externally supplied ERC20 tokens, encountering a USDT-style non-standard token as the underlying is plausible for any CW20-over-ERC20 pointer deployment against a race-protected ERC20.

### Recommendation
When updating the allowance to a new non-zero value on the underlying ERC20 and the previous allowance was non-zero, first call `approve(spender, 0)` before calling `approve(spender, new_allowance)`, mirroring the general mitigation recommended in the referenced report, or switch to a `safeIncreaseAllowance`/`safeDecreaseAllowance`-style delta call if the underlying token exposes one.

### Proof of Concept
1. Deploy a CW20-over-ERC20 pointer contract per `example/cosmwasm/cw20/src/contract.rs` pointing at a mock ERC20 that enforces approval race protection (reverts `approve()` when `allowance(owner, spender) > 0 && amount > 0`).
2. Call `increase_allowance(spender, amount1)` — this succeeds since the initial allowance is `0`, resulting in `approve(spender, amount1)` on the underlying token, leaving a non-zero allowance.
3. Call `increase_allowance(spender, amount2)` again — `new_allowance = amount1 + amount2` is computed and `erc20_approve_payload(spender, new_allowance)` is sent to the underlying ERC20 via `execute_increase_allowance`: [4](#0-3) 
4. The underlying ERC20's `approve()` reverts because the current allowance is non-zero, causing the CW20 message to fail and leaving the pointer's allowance management for that spender permanently stuck at `amount1` until manually reset — which the current code path never does.

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

**File:** example/cosmwasm/cw20/src/contract.rs (L152-189)
```rust
// Decrease the allowance of spender by amount.
// Expiration does not work here since it is not supported by ERC20.
pub fn execute_decrease_allowance(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    spender: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&spender)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    // Query the current allowance for this spender
    let querier = EvmQuerier::new(&deps.querier);
    let current_allowance = querier.erc20_allowance(erc_addr.clone(), info.sender.clone().into_string(), spender.clone())?.allowance;

    // If the new allowance after deduction is negative, set allowance to 0.
    let new_allowance = match current_allowance.checked_sub(amount)
    {
        Ok(new_amount) => new_amount,
        Err(_) => Uint128::MIN,
    };
    
    // Send the message to approve the new amount.
    let payload = querier.erc20_approve_payload(spender.clone(), new_allowance)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };

    let res = Response::new()
        .add_attribute("action", "decrease_allowance")
        .add_attribute("spender", spender)
        .add_attribute("amount", amount)
        .add_attribute("new_allowance", new_allowance)
        .add_attribute("by", info.sender)
        .add_message(msg);

    Ok(res)
}
```

**File:** contracts/src/CW20ERC20Pointer.sol (L59-77)
```text
    function approve(address spender, uint256 amount) public override returns (bool) {
        // if amount is larger uint128 then set amount to uint128 max
        if (amount > type(uint128).max) {
            amount = type(uint128).max;
        }
        uint256 currentAllowance = allowance(msg.sender, spender);
        if (currentAllowance > amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(currentAllowance - amount)));
            string memory req = _curlyBrace(_formatPayload("decrease_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        } else if (currentAllowance < amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount - currentAllowance)));
            string memory req = _curlyBrace(_formatPayload("increase_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        }
        return true;
    }
```
