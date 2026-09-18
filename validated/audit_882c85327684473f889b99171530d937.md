### Title
CW20-to-ERC20 pointer's `approve()` grants excess allowance when front-run by the spender - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer.approve()` emulates the ERC20 "set absolute allowance" semantics on top of a CW20 token, which only exposes `increase_allowance`/`decrease_allowance`. It does this by reading the current allowance live via a CosmWasm query and then issuing a delta message. Because the read and the delta computation are not atomic with respect to the spender's own use of the allowance, a spender can front-run an owner's `approve()` call to first drain the old allowance, causing the pointer to compute the delta as if no allowance had been spent, ultimately granting the spender more total allowance than the owner intended.

### Finding Description
`CW20ERC20Pointer` is the Solidity ERC20-interface pointer contract that sei-chain generates so an EVM contract/user can interact with a native CW20 token through the standard ERC20 ABI. Its `approve()` implementation is: [1](#0-0) 

It queries `allowance(msg.sender, spender)` live from the CW20 contract, then, depending on whether the desired `amount` is above or below `currentAllowance`, issues an `increase_allowance` or `decrease_allowance` CosmWasm message for the difference: [2](#0-1) 

Because the CW20 backend only supports relative adjustments, the pointer relies on `currentAllowance` being an accurate snapshot of what the spender is entitled to at approval time. The spender, however, controls exactly when to consume the existing allowance via `transferFrom`. If the spender front-runs the owner's `approve(spender, newAmount)` transaction with a `transferFrom` call that spends (some or all of) the pre-existing allowance, the on-chain `currentAllowance` read by the pointer during the owner's `approve()` execution will already be reduced (or zero). The pointer then computes `increase_allowance` for `newAmount - currentAllowance`, which is larger than the delta the owner actually intended, and grants it on top of the tokens the spender already extracted using the old allowance. The end state is that the spender walks away with more total value transferred/allowed than the owner's `approve(spender, newAmount)` call was meant to authorize — this is the CW20-pointer-specific instance of the same bug class described in the referenced report: a value read for a later on-chain computation (there, `recipient.proposalBid`; here, `currentAllowance`) can be manipulated by the counterparty between read and use to extract more funds than intended.

This differs from (and is more severe than) the generic, well-known ERC20 "approve race condition," where the spender can at most spend `oldAllowance + newAllowance`. Here, because the pointer recomputes the increase based on a stale/attacker-manipulated `currentAllowance`, the spender can also engineer scenarios (e.g., repeatedly draining then re-approving) that increase the final allowance beyond what a straightforward "old + new" race would yield, since the delta computed is always relative to whatever `currentAllowance` the pointer observes at execution time, not to the amount the owner actually authorized previously.

### Impact Explanation
Any CW20 token that has a pointer contract created via `x/evm/keeper/pointer.go` (a permissionless, user-triggerable operation) is exposed. A spender that is front-running (or simply racing) an owner's `approve()` call can obtain unauthorized extra allowance and transfer more CW20-backed tokens than the token owner intended to authorize, resulting in direct fund loss for the owner. This satisfies the "unauthorized transfer via precompile or pointer" / "concrete fund loss" impact criteria.

### Likelihood Explanation
Any account holding a CW20 pointer's ERC20 allowance can trigger this by front-running (mempool-visible) `approve()` transactions with a `transferFrom` call, which is a standard MEV-style front-run achievable by any unprivileged EVM transaction sender — no special privileges, validator status, or off-chain compromise required.

### Recommendation
Do not derive the allowance delta from a live-queried `currentAllowance` inside `approve()`. Instead, either (a) require `approve()` callers to go through `increaseAllowance`/`decreaseAllowance` with an expected `currentAllowance` parameter (à la OpenZeppelin's mitigated pattern) so the CW20 call fails safely if the assumption is stale, or (b) track allowance state locally in the pointer contract (mirroring standard ERC20 semantics) instead of trusting a live CW20 query taken mid-transaction, so the "set to X" semantics cannot be perturbed by interleaved `transferFrom` calls.

### Proof of Concept
1. Owner has approved Spender for 100 CW20-backed tokens via the pointer (`allowance(owner, spender) == 100`).
2. Owner submits `approve(spender, 50)` intending to reduce the allowance to 50.
3. Spender sees this pending tx and front-runs it with `transferFrom(owner, spender, 100)`, consuming the entire existing allowance (`allowance(owner, spender)` becomes 0), and receiving 100 tokens.
4. Owner's `approve(spender, 50)` now executes: `currentAllowance = allowance(owner, spender) = 0` (fetched live per [3](#0-2) ), which is `< amount (50)`, so the pointer issues `increase_allowance` for `50 - 0 = 50` (per [4](#0-3) ).
5. Final state: Spender has already received 100 tokens AND now holds a fresh allowance of 50, i.e., total value obtained/available (150) exceeds what the owner intended to ever authorize (50 remaining allowance after reducing from 100).

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
