### Title
`CW20ERC20Pointer.approve` frontrunning allows a spender to extract more tokens than the owner intended when changing an allowance - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer.approve()` implements the standard "set absolute allowance" ERC20 semantics on top of a CW20 contract by computing the delta between the currently-queried allowance and the requested target amount, then issuing `increase_allowance`/`decrease_allowance` to the underlying CW20 contract. Because the "current allowance" is read live from chain state at execution time rather than being the value the owner observed when submitting the transaction, a malicious spender can frontrun the owner's allowance-update transaction to drain the old allowance, causing the pointer's delta calculation to convert the owner's intended "set to M" into an unintended net increase, letting the spender pull more tokens in total than the owner ever authorized.

### Finding Description
The pointer's approve implementation is: [1](#0-0) 

It queries `allowance(msg.sender, spender)` on-chain and computes `currentAllowance - amount` or `amount - currentAllowance` to issue `decrease_allowance`/`increase_allowance` calls to the wrapped CW20 contract: [2](#0-1) 

This is functionally equivalent to a classic "overwrite" ERC20 `approve(spender, amount)` (final on-chain allowance == `amount`), which is exactly the pattern the external report identifies as frontrunnable: since the new allowance state depends on the *actual* current allowance at execution time (not the value the owner observed when signing the tx), an attacker who is the `spender` can:

1. Observe the owner's pending `approve(spender, M)` transaction (owner currently has allowance `N` to spender).
2. Frontrun it by calling `transferFrom(owner, attacker, N)` to fully drain the existing allowance `N`.
3. Let the owner's `approve(spender, M)` transaction execute. It re-queries the now-zero allowance and issues `increase_allowance` for the full `M` (instead of the owner's intended net adjustment), setting the on-chain allowance to `M`.
4. Call `transferFrom(owner, attacker, M)` again.

Net result: the attacker extracts `N + M` tokens although the owner only ever intended the final allowance to be `M` (potentially even lower than `N`, i.e., an allowance *decrease*).

### Impact Explanation
This causes direct fund loss for CW20-token owners who use the EVM-side ERC20 pointer to manage allowances for any CW20 asset bridged into the EVM. Because `approve` recomputes deltas from live state rather than atomically overwriting, a malicious `spender` (which is any EVM address the owner has ever approved, e.g., a DEX router, could act maliciously or be compromised) can extract more value than the owner authorized at any point where the owner submits a new `approve` call while a nonzero allowance still exists. This matches the accepted "unauthorized transfer via precompile or pointer" impact category, since `CW20ERC20Pointer` is exactly the CW20↔EVM pointer bridge contract.

### Likelihood Explanation
Likelihood is Medium: it requires (a) the owner to have a live nonzero allowance for a given spender to the pointer, and (b) the owner submitting a new `approve` call to change that allowance while the spender is malicious/adversarial and actively watching the mempool to frontrun. This is a realistic and commonly-triggered pattern for dApp integrations (e.g. routers/DEXes) that call `approve` repeatedly as part of normal usage, exactly the scenario flagged in the original report.

### Recommendation
Change `CW20ERC20Pointer.approve()` (and any equivalent pointer/native ERC20 approve implementations) to avoid computing deltas from queried live state as an implicit "set absolute allowance" operation. Either:
- Add `increaseAllowance`/`decreaseAllowance` methods that take an explicit delta from the caller (rather than deriving the delta from a live query against a target absolute value), and deprecate/guard `approve` the same way `safeApprove` guards it (only allow updating a non-zero allowance to zero, forcing two-step "N → 0 → M" changes), or
- Have `approve` only allow transitioning to/from zero atomically in one call and require callers to zero out non-zero allowances before setting a new non-zero value, consistent with the recommendation in the original report.

### Proof of Concept
1. Owner `O` has an existing CW20 allowance of `N` for spender `S` on the pointer contract (`CW20ERC20Pointer`).
2. `O` submits `approve(S, M)` intending the final allowance to become `M`.
3. `S` frontruns by submitting `transferFrom(O, S, N)`, draining the full existing allowance to zero before `O`'s tx lands.
4. `O`'s `approve(S, M)` executes: `allowance(O, S)` is now `0`, so the pointer issues `increase_allowance` for the full `M`, i.e. allowance becomes `M` (see `contracts/src/CW20ERC20Pointer.sol:70-75`).
5. `S` now calls `transferFrom(O, S, M)`, extracting an additional `M` tokens.
6. Total extracted by `S`: `N + M`, exceeding the `M` the owner intended as the maximum exposure at any point in time.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L50-77)
```text
    function allowance(address owner, address spender) public view override returns (uint256) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory s = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
        string memory req = _curlyBrace(_formatPayload("allowance", _curlyBrace(_join(o, s, ","))));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "allowance");
    }

    // Transactions
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
