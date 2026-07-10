### Title
Truncation of `token_id_hash` to 128-bit `low` Half Used as Deployment Salt Enables Token-Mapping Collision — (`File: starknet/src/omni_bridge.cairo`)

### Summary

In `deploy_token`, the Starknet bridge computes a full 256-bit keccak hash of the NEAR token ID string, then silently discards the upper 128 bits (`high`) when deriving the `deploy_syscall` salt. Two distinct NEAR token IDs whose keccak hashes share the same `low` 128-bit half will produce the same deterministic contract address, causing the second deployment to panic (permanent DoS for that token) or — if the constructor calldata also matches — to silently overwrite the `starknet_to_near_token` reverse mapping, breaking the bridge's token-accounting invariant.

### Finding Description

In `starknet/src/omni_bridge.cairo`, `deploy_token` computes:

```cairo
let token_id_hash = compute_keccak_byte_array(@payload.token);  // u256
let salt: felt252 = token_id_hash.low.into();                   // only low 128 bits
let (contract_address, _) = deploy_syscall(
    self.bridge_token_class_hash.read(), salt, constructor_calldata.span(), false,
).unwrap_syscall();
```

The collision-guard check uses the **full** `u256` hash as the map key:

```cairo
let existing_token = self.near_to_starknet_token.read(token_id_hash);
assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

So two token IDs `A` and `B` where `keccak(A).low == keccak(B).low` but `keccak(A).high != keccak(B).high` will:
1. Pass the `ERR_TOKEN_ALREADY_DEPLOYED` guard (different `u256` keys).
2. Attempt `deploy_syscall` with the **same** salt → Starknet will panic because the address is already occupied.

This permanently prevents token `B` from ever being deployed on Starknet, freezing all cross-chain transfers for that token.

Additionally, the `starknet_to_near_token` reverse mapping is keyed by `contract_address`. If the second deployment somehow succeeded (e.g., different `constructor_calldata` producing a different address despite the same salt — which is not the case here, but illustrates the design fragility), the reverse mapping would be overwritten, causing `is_bridge_token` to return the wrong NEAR token ID and misdirecting `init_transfer` accounting.

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds for the colliding token.** Once token `A` is deployed, any NEAR-signed `deploy_token` call for token `B` (with the same `low` half) will always revert at `deploy_syscall`. The NEAR bridge will have signed and emitted the deployment message, but it can never be finalized on Starknet. All subsequent `fin_transfer` calls for token `B` will also fail because the token contract does not exist at the expected address, permanently locking any assets bridged from NEAR to Starknet for that token.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

A keccak-256 collision in the lower 128 bits requires finding two strings whose keccak outputs share the same 128-bit suffix. The birthday bound for a 128-bit space is approximately 2^64 token deployments — far beyond practical reach for random token IDs. However:

- The NEAR token ID namespace is an open string (any `AccountId`), and the bridge is permissionless for `deploy_token` (anyone with a valid MPC signature can call it).
- The MPC signer signs any valid NEAR token ID. An attacker who can influence which token IDs are registered on NEAR (e.g., by registering a crafted account ID) could search for a collision offline and then submit both deployment requests.
- Even without a deliberate collision, the design silently discards 128 bits of entropy, which is a structural weakness that violates the stated invariant "Deterministic token addresses (same token ID → same address)" — the converse (different token IDs → different addresses) is not guaranteed.

Likelihood is **medium** for a targeted attack by a motivated adversary with control over NEAR account ID registration; **low** for accidental collision.

### Recommendation

Use the full `u256` hash to derive the salt, or combine both halves into a single `felt252`-compatible value. Since `felt252` cannot hold a full `u256`, the recommended approach is to use a Pedersen or Poseidon hash of the full keccak output (both `high` and `low`) to produce a single `felt252` salt:

```cairo
// Instead of:
let salt: felt252 = token_id_hash.low.into();

// Use both halves, e.g. via Poseidon:
use core::poseidon::poseidon_hash_span;
let salt: felt252 = poseidon_hash_span(
    array![token_id_hash.low.into(), token_id_hash.high.into()].span()
);
```

This preserves the full 256-bit entropy of the keccak output within the felt252 domain and eliminates the truncation collision surface.

### Proof of Concept

1. Find (offline) two NEAR token IDs `A` and `B` such that `keccak(A).low == keccak(B).low` and `keccak(A).high != keccak(B).high`. (Requires ~2^64 hash evaluations — feasible for a well-resourced attacker.)
2. Obtain a valid MPC signature for `deploy_token` payload for token `A`. Deploy token `A` successfully. The contract is deployed at address `addr = hash(bridge_class_hash, salt=keccak(A).low, ...)`.
3. Obtain a valid MPC signature for `deploy_token` payload for token `B`. Submit `deploy_token` for token `B`.
4. The guard `self.near_to_starknet_token.read(keccak(B))` returns zero (different `u256` key), so the `ERR_TOKEN_ALREADY_DEPLOYED` assert passes.
5. `deploy_syscall` is called with the same `salt = keccak(B).low = keccak(A).low`. Starknet reverts because the address is already occupied.
6. Token `B` can never be deployed. All `fin_transfer` calls for token `B` on Starknet revert permanently, freezing bridged assets.

**Root cause line:** [1](#0-0) 

**Collision guard uses full u256 (inconsistent with salt derivation):** [2](#0-1) 

**Acknowledged design decision (confirms the truncation is intentional but the collision risk is not addressed):** [3](#0-2)

### Citations

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** starknet/src/omni_bridge.cairo (L217-222)
```text
            // Use the low part of the u256 hash to ensure it fits in felt252
            let salt: felt252 = token_id_hash.low.into();
            let (contract_address, _) = deploy_syscall(
                self.bridge_token_class_hash.read(), salt, constructor_calldata.span(), false,
            )
                .unwrap_syscall();
```

**File:** starknet/CLAUDE.md (L47-47)
```markdown
3. **Salt uses low 128 bits**: Full u256 hash doesn't fit in felt252
```
