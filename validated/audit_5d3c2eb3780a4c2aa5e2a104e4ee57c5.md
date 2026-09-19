I have enough detail confirmed. This is a valid analog: `CW20ERC20Pointer.approve()` implements a "safe-looking" approve using CW20's increase/decrease allowance messages, but the calculation is based on an allowance value that is queried mid-transaction, so it collapses to a plain "set to target amount" semantics — providing zero actual protection against the classic approve-race front-running attack.

### Title
ERC20 `approve` race condition in `CW20ERC20Pointer` enables spender to overspend combined old+new allowance - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer.approve()` (the ERC20-compatible EVM wrapper that Sei deploys for any CW20 token, reachable by any EVM/pointer user) computes a delta between the currently stored CW20 allowance and the requested new amount, then issues a CW20 `increase_allowance`/`decrease_allowance` message for that delta. This is meant to emulate `IERC20.approve(spender, amount)`, but because the "current allowance" is only read at execution time, the function is functionally equivalent to unconditionally setting the allowance to `amount` — it inherits the exact classic ERC20 approve-race/"multiple withdrawal" vulnerability described in the reference report, with no actual mitigation despite superficially resembling one.

### Finding Description
`approve()` in `contracts/src/CW20ERC20Pointer.sol`: [1](#0-0) 

reads `currentAllowance = allowance(msg.sender, spender)` and then issues `increase_allowance`/`decrease_allowance` for `|amount - currentAllowance|`. Regardless of what `currentAllowance` is at execution time (including if it was already partially or fully consumed by a spender's `transferFrom` that landed between the owner's transaction being broadcast and mined), the CW20 contract's allowance ends up set to exactly `amount` after execution — i.e., this is behaviorally identical to a naive `allowance = amount` assignment, which is precisely the pattern OpenZeppelin/EIP-20 authors warn is unsafe.

A spender who is granted an allowance can watch the mempool for a pending `approve(spender, newAmount)` transaction from the owner (e.g., raising allowance from 100 to 200), front-run it by calling `transferFrom` to consume the existing 100 allowance, and then, after the owner's `approve` transaction executes and resets the allowance to 200, spend the new 200 as well — extracting 300 total instead of the 200 the owner intended to authorize. The intermediate re-query of `currentAllowance` gives no protection because it doesn't distinguish "consumed by front-run" from "never granted."

`transferFrom` itself directly forwards to the CW20 `transfer_from` message with no additional guard: [2](#0-1) 

### Impact Explanation
This constitutes unauthorized transfer via a CW<->EVM pointer contract: an approved spender can extract more tokens than the owner ever intended to authorize at any single point in time, resulting in direct fund loss for the CW20 token owner who uses the standard `approve` pattern to adjust an existing allowance (a legitimate, ordinary use case for allowance management, e.g., adjusting a DEX/router allowance). This matches the accepted impact class of "unauthorized transfer via precompile or pointer."

### Likelihood Explanation
`CW20ERC20Pointer` contracts are deployed for arbitrary CW20 tokens and are reachable by any EVM public-RPC client/contract caller; front-running via mempool observation and gas-price ordering is a standard MEV/searcher technique on EVM-compatible chains, requiring no special privilege, validator, or node position. Any token owner who calls `approve` to change (rather than only ever reset-to-zero-then-set) an existing non-zero allowance to a previously-approved spender is exposed.

### Recommendation
Do not overwrite/derive the target allowance from a live `currentAllowance()` re-query at the time of `approve`. Instead, adopt the standard mitigation: require callers to first set the allowance to zero before setting a new non-zero value (revert if `currentAllowance != 0 && amount != 0`), or expose non-standard `increaseAllowance`/`decreaseAllowance` entry points (analogous to what the CW20 contract itself exposes) that atomically add/subtract a caller-specified delta instead of computing a delta from a racy `amount` vs `currentAllowance` comparison inside `approve`.

### Proof of Concept
1. Owner calls `CW20ERC20Pointer.approve(spender, 100)` → CW20 allowance(owner, spender) = 100.
2. Owner decides to raise it and submits `approve(spender, 200)`.
3. Spender observes the pending tx in mempool, front-runs with higher gas price calling `transferFrom(owner, spender, 100)` — consumes the full 100 allowance, CW20 allowance is now 0.
4. Owner's `approve(spender, 200)` executes: `currentAllowance()` returns 0 (post front-run), so `_execute` issues `increase_allowance(spender, 200)` → CW20 allowance becomes 200.
5. Spender now calls `transferFrom(owner, spender, 200)`, successfully draining another 200.
6. Total drained: 300, exceeding the 200 the owner ever intended to have outstanding at once — reproducing the classic ERC20 approve race with no mitigation from the pointer's delta-based logic.

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

**File:** contracts/src/CW20ERC20Pointer.sol (L88-96)
```text
    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```
