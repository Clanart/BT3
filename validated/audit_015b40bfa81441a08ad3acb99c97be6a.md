## Title
Front-runnable `approve()` in `CW20ERC20Pointer` can flip a reduce-allowance call into an allowance *increase*, allowing a spender to obtain unintended extra CW20 allowance - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer.approve()` implements the standard ERC20 "set absolute allowance" semantics on top of CW20's relative `increase_allowance`/`decrease_allowance` messages by reading the *live* current allowance at execution time and computing a delta. Because CW20 only supports relative allowance changes, and the delta is computed from on-chain state read during transaction execution (not from the state the caller observed when composing the call), a spender can front-run an owner's allowance-lowering transaction to change the on-chain allowance in between; when the owner's transaction is finally mined, the contract recomputes the delta against the now-different allowance, and can end up sending an `increase_allowance` message instead of the intended `decrease_allowance`, silently granting the spender additional CW20 allowance the owner never approved.

### Finding Description
`approve()` reads `currentAllowance` via `allowance(msg.sender, spender)` at execution time, then decides whether to call `increase_allowance` or `decrease_allowance` on the underlying CW20 contract based on comparing `currentAllowance` to the requested absolute `amount`: [1](#0-0) 

This mirrors exactly the class of bug in the referenced report (`FootiumEscrow#setApprovalForERC20` -> `erc20.approve()`), but is actually a stronger variant: instead of merely allowing a spender to spend both the old and new allowance amount, the branch logic itself is state-dependent, so a value that the owner submitted as a "decrease" transaction can be reinterpreted as an "increase" transaction if the observed on-chain allowance changes between transaction submission and mining, e.g. via a spender front-running with `transferFrom`.

Scenario:
1. Owner sets allowance for Bob to 1000 via `approve(Bob, 1000)`. Underlying CW20 allowance = 1000.
2. Owner decides to reduce it and submits `approve(Bob, 500)`. This transaction, when it executes, will read the *then-current* allowance and compute `currentAllowance - 500` (decrease) or `500 - currentAllowance` (increase) depending on what it observes at execution time.
3. Bob sees the pending "lower approval" transaction and front-runs it with `transferFrom(owner, bob, 1000)`, consuming the entire allowance and driving on-chain allowance to 0 before Owner's transaction is mined.
4. When Owner's `approve(Bob, 500)` transaction finally executes, `currentAllowance` is now 0 (post Bob's spend), so the code takes the `currentAllowance < amount` branch and issues an `increase_allowance(spender, 500)` message — the opposite of Owner's intent. Bob now has an additional 500 allowance that Owner never approved, on top of already having spent 1000 tokens.

This lets a spender combine a classic approve front-run with an unintended-increase to extract more value/allowance than the owner ever intended to grant across a sequence of two `approve` calls, purely by racing the mempool with a `transferFrom` call.

### Impact Explanation
This affects `CW20ERC20Pointer`, a production EVM-side pointer contract that exposes CW20 tokens as ERC20 tokens to EVM contracts/wallets. Any DeFi integration, router, or wallet that treats this pointer as a standard ERC20 (and uses the classic "approve differing amount" pattern, which is extremely common, e.g. resetting allowance before setting a new one) is exposed. A malicious or opportunistic spender contract can monitor the mempool and, by front-running an owner's `approve` call intended to lower or reset an allowance, cause the pointer to issue an unintended `increase_allowance` CosmWasm message, resulting in unauthorized allowance being granted and enabling unauthorized transfer of the underlying CW20-bridged tokens via the pointer — a concrete instance of unauthorized transfer via a CW<->EVM pointer.

### Likelihood Explanation
The bug is triggerable by any unprivileged EVM transaction sender who is also acting as (or colludes with) the approved spender, requires no special permissions, and only depends on standard mempool front-running which is a routine capability on EVM-compatible chains. The pattern of "approve to X, then later approve to Y<X" (or 0) to update an allowance is an extremely common integration pattern (e.g., resetting allowance before increasing it for a different amount, revoking access, etc.), making the vulnerable code path realistically reachable.

### Recommendation
- Do not derive the `increase_allowance`/`decrease_allowance` direction from a live re-query of on-chain allowance during the same transaction that is meant to enact a caller-observed target value. Instead, require the caller to pass the expected current allowance (similar to `increaseAllowance`/`decreaseAllowance` patterns), and revert if the on-chain state has changed since observation, i.e., a compare-and-swap style check like `require(currentAllowance == expectedOldAllowance)` before mutating.
- Alternatively, expose `increaseAllowance(spender, addedValue)` and `decreaseAllowance(spender, subtractedValue)` directly (matching CW20 semantics) rather than emulating the ERC20 `approve(spender, amount)` "set absolute value" interface, and discourage/deprecate `approve()` for allowance changes, consistent with the standard OpenZeppelin recommendation for ERC20 approve race conditions.
- Apply the same fix to the analogous Rust-side wrapper `execute_increase_allowance` / `execute_decrease_allowance` in `example/cosmwasm/cw20/src/contract.rs`, which has the identical query-then-derive-delta race pattern for the reverse (CW20-wrapping-ERC20) direction. [2](#0-1) [3](#0-2) 

### Proof of Concept
1. Owner deploys/uses an existing `CW20ERC20Pointer` for a CW20 token and calls `approve(Bob, 1000)`; underlying CW20 allowance becomes 1000. [4](#0-3) 
2. Owner submits `approve(Bob, 500)` intending to lower the allowance to 500.
3. Bob observes the pending transaction in the mempool and submits `transferFrom(owner, bob, 1000)` with a higher gas price/priority so it is included first, draining the allowance to 0.
4. Owner's `approve(Bob, 500)` transaction executes afterward: `allowance(owner, Bob)` now returns 0, so `currentAllowance (0) < amount (500)` is true, and the contract issues `increase_allowance(Bob, 500)` instead of a decrease — Bob unexpectedly receives a fresh 500 allowance despite Owner intending to shrink it, in addition to the 1000 already transferred out. [5](#0-4)

### Citations

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

**File:** example/cosmwasm/cw20/src/contract.rs (L116-150)
```rust
// Increase the allowance of spender by amount.
// Expiration does not work here since it is not supported by ERC20.
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
