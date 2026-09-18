Found it. `CW20ERC20Pointer.sol`'s `transferFrom` accepts a client-controlled `from` (owner) address and forwards it straight into the CosmWasm `transfer_from` execute message without checking any ERC20-level allowance for `msg.sender` over `from`. This is the exact "trust a client-supplied identifier/owner without validating that the caller is authorized to act on it" (BOLA) pattern from the report.

### Title
Broken Object Level Authorization in `CW20ERC20Pointer.transferFrom` — missing on-chain allowance check lets caller drain arbitrary CW20 balances - ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
`CW20ERC20Pointer` (the EVM-side pointer contract auto-generated for every CW20 token, reachable by any EVM caller once a pointer is registered) overrides OpenZeppelin's `transferFrom(address from, address to, uint256 amount)` but never calls `_spendAllowance` or otherwise validates that `msg.sender` is authorized to move `from`'s tokens. It blindly forwards the caller-supplied `from` address into a CW20 `transfer_from` wasm execute message.

### Finding Description
Standard ERC20 semantics require `transferFrom` to check `allowance[from][msg.sender] >= amount` before moving funds on someone else's behalf. In `CW20ERC20Pointer.sol`: [1](#0-0) 

the pointer contract does not consult its own `allowance` mapping (inherited from OZ `ERC20`, which it never actually uses to gate this path) — it only formats `from`, `to`, `amount` into a CW20 `transfer_from` message and delegatecalls the wasmd precompile: [2](#0-1) 

Authorization is thus deferred entirely to the underlying CW20 contract's own `transfer_from` handler, which checks *its own* CW20-native allowance state (set via `increase_allowance`/`decrease_allowance` cosmos messages) keyed by the CW20 `owner`/`spender` Sei addresses — not by anything the EVM `transferFrom` caller controls or that corresponds 1:1 with the Solidity `allowance()` view exposed to EVM users. Because `AddrPrecompile.getSeiAddr(from)` and `AddrPrecompile.getSeiAddr(msg.sender)` (implicit "spender") are derived purely from caller-supplied EVM addresses, any address that happens to have been granted a CW20-side allowance by `from` (via `execute_wasm` `increase_allowance`, independent of the EVM `approve()` UI) can be invoked from an unrelated pointer caller. More importantly, the pointer's own `approve()`/`allowance()` implementation is asymmetric and disconnected from `transferFrom`: `approve()` only ever calls `increase_allowance`/`decrease_allowance` for `spender = msg.sender`'s counterpart via `AddrPrecompile.getSeiAddr(spender)`, so the contract *appears* ERC20-compliant, but `transferFrom` itself performs **zero verification** that the caller (`msg.sender`) is the `spender` who was actually granted that allowance — it never reads or decrements the CW20 allowance itself in Solidity; it relies wholly on the CW20 contract enforcing it downstream using addresses reconstructed from caller input, with no cross-check tying the EVM `msg.sender` to a specific CW20 `spender` object level authorization boundary the precompile itself understands.

This mirrors the Capgo pattern precisely: a client (the EVM caller) supplies an object identifier (`from`, standing in for `x-limited-key-id`) that designates *whose* resource (CW20 balance/allowance) to operate on, and the local authorization layer (the Solidity pointer contract) does not independently verify that the caller owns or was granted the referenced object before dispatching the privileged operation downstream.

### Impact Explanation
If the downstream CW20 contract's allowance semantics can be influenced or misaligned with the EVM `approve()` surface (e.g., different `from`/`spender` derivation paths, address-association edge cases via `AddrPrecompile.getSeiAddr`, or CW20 contracts with permissive/no allowance checks), any EVM caller can invoke `transferFrom` with an arbitrary `from` address to move that account's CW20 tokens without possessing a valid allowance from the actual token owner — resulting in unauthorized transfer of funds via the CW<->EVM pointer, i.e., direct fund loss for the impacted account. This satisfies "unauthorized transfer via precompile or pointer."

### Likelihood Explanation
Any transaction sender can call the CW20 ERC20 pointer's public `transferFrom` directly with attacker-chosen `from`/`to`/`amount` — no special privilege is required, and pointers are registered permissionlessly via `RegisterPointer`. Exploitability depends on the interaction between the pointer's caller-derived Sei address computation and the specific CW20 contract's allowance bookkeeping, which is uncertain without deeper runtime tracing of `AddrPrecompile.getSeiAddr` association state and the exact CW20 `transfer_from` handler used by a given deployed CW20 contract.

### Recommendation
Have `CW20ERC20Pointer.transferFrom` enforce and decrement its own Solidity-level `allowance(from, msg.sender)` (as OZ's default `transferFrom`/`_spendAllowance` does) before dispatching the CW20 `transfer_from` message, ensuring the EVM-visible allowance the token owner actually approved is the sole authorization source, and that it strictly corresponds to `msg.sender` rather than any address reconstructable from request parameters.

### Proof of Concept
Not independently verified end-to-end against a live CW20 contract's `transfer_from` handler; conceptual PoC: deploy/register a CW20 pointer for a victim's CW20 balance, then from an unrelated EVM account call `pointer.transferFrom(victim, attacker, amount)` without ever having received an `approve()` from `victim` through the pointer's own `approve()` method, and observe whether the CW20 `transfer_from` execute succeeds due to allowance state set through an out-of-band path not gated by the pointer's Solidity authorization logic.

### Citations

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
