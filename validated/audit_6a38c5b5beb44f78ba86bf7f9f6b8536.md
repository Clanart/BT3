### Title
Front-runnable allowance race condition in `CW20ERC20Pointer.approve()` allows a malicious spender to double-spend the token owner's allowance - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer` is the ERC20-interface pointer contract that lets EVM callers interact with a CW20 token through the wasmd precompile. Its `approve()` function tries to translate a "set allowance to `amount`" EVM call into CosmWasm `increase_allowance`/`decrease_allowance` messages by first reading the *current on-chain* allowance and then issuing a delta message. Because the delta is computed from live on-chain state at execution time rather than from the state the owner observed when signing the transaction, a spender can front-run an owner's allowance-lowering transaction and cause the owner to grant strictly more tokens than intended — the same classic ERC20 `approve()` race described in the reference report.

### Finding Description
`approve()` in `CW20ERC20Pointer.sol` queries the current allowance and issues an `increase_allowance`/`decrease_allowance` CW20 message for the difference: [1](#0-0) 

Consider owner O who previously approved spender S for `M` tokens, and now wants to reduce it to `N < M` by calling `approve(S, N)`.

1. Attacker/spender S observes the pending `approve(S, N)` transaction in the mempool.
2. S front-runs it with `transferFrom(O, S, M)`, spending the entire existing allowance `M`; on-chain CW20 allowance becomes `0`.
3. O's `approve(S, N)` transaction is now mined. Inside `approve()`, `currentAllowance = allowance(msg.sender, spender)` now reads `0` (post front-run), so the branch `currentAllowance < amount` fires and the contract issues `increase_allowance(spender, N - 0)`, i.e. it *adds* `N` to the CW20 allowance rather than *setting* it to `N`. Final on-chain allowance becomes `N`.
4. S then calls `transferFrom(O, S, N)` again.

Total tokens spent by S: `M + N`, exceeding the `N` (or even the original `M`) that O ever intended to allow at any single point in time. This exactly reproduces the vulnerability class from the referenced Footium report: the contract emulates `approve()` semantics via a read-then-delta pattern, which is exactly as race-prone as calling raw `ERC20.approve()` directly, because the "read" happens at transaction-execution time (after any front-running), not at signing time.

Compare with the standard, non-racy alternative used elsewhere in the same codebase (`increaseAllowance`/`decreaseAllowance` cw20 execute handlers), which are explicit, additive/subtractive primitives with no ambiguity — the pointer's `approve()` recreates the race by combining a read of mutable state with an inferred delta. [2](#0-1) 

### Impact Explanation
Any CW20 token holder who uses `CW20ERC20Pointer.approve()` to modify (especially reduce) an existing allowance is exposed to a malicious spender extracting more tokens than the owner ever intended to have outstanding at once. This is a direct, unprivileged-attacker-reachable path to token loss for the owner, mediated entirely through the pointer contract that Sei deploys for every CW20 token exposed on the EVM side. Because pointer contracts are core, chain-provided infrastructure for CW20<->EVM interoperability (not a third-party dApp), this qualifies as a fund-loss issue reachable from a standard EVM transaction/contract call.

### Likelihood Explanation
Exploitation requires only that: (1) an owner has previously granted a non-zero allowance to a spender, and (2) the owner submits a follow-up `approve()` call to change (typically lower) that allowance while it is still outstanding. A malicious or compromised spender monitoring the public mempool can trivially front-run the owner's transaction. This does not require any privileged access — only being the approved spender and observing pending transactions, which is a common capability for any EVM-compatible chain including Sei.

### Recommendation
Do not compute the increase/decrease delta from a live `allowance()` read inside `approve()`. Instead:
- Expose explicit `increaseAllowance(spender, addedValue)` / `decreaseAllowance(spender, subtractedValue)` functions on the pointer (mirroring the underlying CW20 primitives) and encourage/require their use instead of `approve()` for allowance changes, or
- Have `approve()` pass an "expected current allowance" check through to the CW20 message (rejecting if the live allowance changed since query), so a stale read cannot be exploited, or
- At minimum, document/require callers to always reset allowance to `0` before setting a new nonzero value, consistent with general ERC20 guidance, and consider adding this safety in the pointer contract itself by requiring a two-step reset-then-set within `approve()`.

### Proof of Concept
1. Owner `O` calls `pointer.approve(S, M)` — CW20 allowance(O,S) becomes `M`.
2. Owner `O` decides to reduce allowance and submits `pointer.approve(S, N)` with `N < M`.
3. Spender `S`, seeing the pending tx, front-runs with `pointer.transferFrom(O, S, M)` — CW20 allowance(O,S) becomes `0`; `S` receives `M` tokens.
4. Owner's `approve(S, N)` executes: `currentAllowance = allowance(O, S) == 0`; since `0 < N`, contract issues `increase_allowance(S, N)`; CW20 allowance(O,S) becomes `N`.
5. Spender `S` calls `pointer.transferFrom(O, S, N)` — receives another `N` tokens.
6. Total received by `S`: `M + N`, more than owner ever intended to have outstanding, causing loss of funds to the owner beyond the approved amount at any single point in time. [1](#0-0)

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
