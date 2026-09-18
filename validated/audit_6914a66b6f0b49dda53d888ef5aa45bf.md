### Title
Approve race condition in `CW20ERC20Pointer.approve()` allows allowance double-spend via front-running - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer` is a production EVM-side ERC20 wrapper that any EVM user can call to interact with an underlying CW20 token through the wasmd precompile. Its `approve(address spender, uint256 amount)` override computes a delta between the *live* allowance and the caller-supplied target amount and issues either an `increase_allowance` or `decrease_allowance` CosmWasm message to reach that target. Because the target is an absolute value and the delta calculation is re-evaluated at execution time (after any prior transaction has already been applied), the classic ERC20 approve-race / front-running double-spend still applies exactly as described in the external report, even though the implementation superficially resembles the "increase/decrease allowance" mitigation.

### Finding Description
`approve()` queries `allowance(msg.sender, spender)` inline and computes the increase or decrease needed to reach `amount`: [1](#0-0) 

This still implements "set absolute allowance" semantics from the caller's perspective (owner intends "make my allowance to `spender` equal to X"), and the increase/decrease is only an implementation detail to talk to the underlying CW20 contract (which only exposes relative `increase_allowance`/`decrease_allowance` messages, per `example/cosmwasm/cw20/src/contract.rs`): [2](#0-1) 

Consider the same scenario as the report:
1. Owner calls `approve(spender, 100)` → allowance is 0, so `increase_allowance(100)` executes, allowance becomes 100.
2. Owner decides to reduce trust and submits `approve(spender, 50)` to the mempool.
3. `spender` observes this pending tx and front-runs with `transferFrom(owner, spender, 100)`, consuming the full old allowance (allowance becomes 0).
4. Owner's `approve(spender, 50)` tx now executes. It re-queries the *live* allowance, which is now 0 (not 100, since the front-run already landed), computes `100... wait, delta = 50 - 0 = 50`, and issues `increase_allowance(50)`, setting the allowance to 50.
5. `spender` then calls `transferFrom(owner, spender, 50)` and drains the newly granted 50.

Net effect: spender extracted 150 total tokens, even though the owner never intended to authorize more than 100 at any point in time — identical outcome to the vulnerability in `FootiumEscrow.setApprovalForERC20`. The intermediate "increase/decrease" bookkeeping does not close the race because the target value (`amount`) is fixed by the caller regardless of what happens to the allowance between submission and execution.

### Impact Explanation
Any club/owner-equivalent actor (a pointer-contract token owner) that changes an ERC20 allowance for an untrusted or semi-trusted spender on a CW20↔EVM pointer token can have funds double-spent beyond the intended authorized amount, resulting in direct fund loss for the token owner. Since `CW20ERC20Pointer` is a general-purpose pointer contract usable by any EVM account for any CW20 token, this is reachable by any public RPC/EVM transaction sender.

### Likelihood Explanation
Exploitation requires the attacker to be the current approved `spender` (or collude with it) and to observe/front-run the owner's allowance-changing transaction in the mempool — a standard, low-cost MEV/front-running technique, making this a realistic and repeatable attack whenever allowances are adjusted (not just initially set) on these pointer tokens.

### Recommendation
Do not derive the message to send from a live-queried allowance combined with a caller-supplied absolute target. Instead, expose (or internally use) `increaseAllowance`/`decreaseAllowance` semantics that take the *delta* directly from the caller (matching OpenZeppelin's `safeIncreaseAllowance`/`safeDecreaseAllowance` pattern), or require the caller to first reduce the allowance to zero before increasing it, so that no transaction re-derives its effect from a value that can change between submission and execution.

### Proof of Concept
1. Owner deploys/uses a `CW20ERC20Pointer` instance and calls `approve(spenderAddr, 100)`.
2. Owner submits `approve(spenderAddr, 50)` to reduce the allowance.
3. Before it's mined, `spenderAddr` submits `transferFrom(owner, spenderAddr, 100)` with higher gas/priority so it lands first, draining the original 100.
4. Owner's `approve(spenderAddr, 50)` executes, finds current allowance is 0 (already drained), computes delta `50 - 0 = 50`, and calls `increase_allowance(spenderAddr, 50)` on the underlying CW20 contract — see the branch at [3](#0-2) .
5. `spenderAddr` calls `transferFrom(owner, spenderAddr, 50)` again, successfully extracting an additional 50 tokens — total 150 extracted versus the 100 max the owner ever intended to authorize at one time.

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
