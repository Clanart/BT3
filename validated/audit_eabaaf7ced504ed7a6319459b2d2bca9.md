### Title
CW20 pointer `approve()` race condition allows spender to combine old and new allowance via front-running — ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
The `approve()` function of `CW20ERC20Pointer` (and the equivalent `ERC20toCW20Pointer`/native pointer contracts) does not set the CW20 allowance directly. Instead, it first queries the *current* allowance and then issues an `increase_allowance` or `decrease_allowance` message for the delta between the current and requested amount. A spender who is front-running the owner's `approve()` call can consume the pre-existing (stale) allowance via `transferFrom` before the owner's adjustment lands, and then still benefit from the newly set allowance afterward — letting the spender transfer more tokens in total than the owner ever intended to authorize at any single point in time.

### Finding Description
`approve()` in `contracts/src/CW20ERC20Pointer.sol` works like this: [1](#0-0) 

It reads `currentAllowance = allowance(msg.sender, spender)` and then sends either an `increase_allowance` or `decrease_allowance` CosmWasm message for `|amount - currentAllowance|` to the underlying CW20 contract, rather than atomically overwriting the stored value with `amount`.

This mirrors the classic ERC20 "approve race condition" bug class described in the reference report: a value that a counterparty relies on (here, the *effective* future allowance) can be manipulated by a front-running transaction that exploits the still-valid stale value before the owner's update transaction is included. Concretely:
1. Owner has previously approved `spender` for `X` tokens.
2. Owner submits `approve(spender, Y)` (`Y < X`) intending to shrink the allowance to `Y`.
3. `spender` observes the pending transaction and front-runs it with `transferFrom(owner, spender, X)`, draining the full old allowance `X` before the owner's `decrease_allowance` message executes.
4. The owner's `approve(spender, Y)` still executes afterward and grants an *additional* `Y` allowance on top of what was already spent.

Net effect: the spender extracts `X + Y` in total value even though the owner never authorized more than `X` at any single instant, and intended to reduce authorization to `Y`.

The same increase/decrease-by-delta pattern also exists in the CosmWasm reference implementation used by the pointer's counterpart contract: [2](#0-1) [3](#0-2) 

Both are reachable by any public EVM/CosmWasm user through the pointer contracts, which are permissionless bridges between ERC20 and CW20 (`x/evm/artifacts/cw20/CW20ERC20Pointer.abi`), i.e., this is directly exploitable by an unprivileged transaction sender via public RPC.

### Impact Explanation
An attacker who is the `spender` of an approval can extract more tokens than the owner intends to authorize by monitoring the mempool and front-running an `approve()` call that lowers (or fully revokes, `amount = 0`) their allowance. This results in concrete unauthorized transfer of ERC20/CW20 pointer-wrapped funds beyond what the token owner intended to expose at any point in time — a direct fund-loss vector via the CW20↔ERC20 pointer bridge.

### Likelihood Explanation
Exploitation requires only that the attacker already holds a non-zero allowance from a victim and monitors the public mempool for the victim's `approve()` transaction attempting to lower it — a standard, low-cost front-running setup requiring no special privileges, achievable by any public RPC client submitting ordinary transactions.

### Recommendation
Change `approve()` to directly set the CW20 allowance to the exact target value in a single atomic operation (e.g., by first resetting to zero and then setting the exact target, or by using a CW20 message type that performs an absolute `set_allowance` rather than a relative increase/decrease), or require callers to use `increaseAllowance`/`decreaseAllowance` semantics explicitly instead of computing a delta from a stale `allowance()` read inside `approve()`.

### Proof of Concept
1. Owner calls `approve(spender, 100)`; CW20 allowance becomes `100`.
2. Owner decides to reduce trust and submits `approve(spender, 10)`.
3. `spender` sees the pending tx, front-runs with `transferFrom(owner, spender, 100)`, draining the full old allowance before the owner's tx lands.
4. Owner's `approve(spender, 10)` executes afterward (`currentAllowance` is now `0`, so it issues `increase_allowance` of `10`), granting a fresh allowance of `10`.
5. Total extracted by spender: `100 + 10 = 110`, exceeding what the owner ever intended to expose (`100` at peak, intended to shrink to `10`).

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
