### Title
Wallet RPC `execute_signing_instructions` is an unrestricted blind-signing oracle over the wallet's master private key — ([File: chia/wallet/wallet_signer.py])

### Summary
The `multiCall`/`delegatecall` bug class is "an entry point takes attacker-supplied instructions and executes them with the contract's own elevated authority, with no restriction on what those instructions can do." The Chia analog is `WalletSigner.execute_signing_instructions()`, reachable via the wallet RPC endpoint `execute_signing_instructions`. It accepts caller-supplied `PathHint.path` (an arbitrary BIP32 derivation path) and `SigningTarget.message` (arbitrary bytes) and, without validating that the message corresponds to any real coin spend or that the caller is entitled to that specific key, derives the private key at the requested path from the wallet's root private key and signs the exact attacker-chosen bytes.

### Finding Description
`WalletSigner.execute_signing_instructions()` in [1](#0-0)  processes `signing_instructions.key_hints.path_hints` by deriving child keys directly from `self.root_private_key` using a path array supplied entirely by the caller:

```
path = [int(step) for step in path_hint.path]
derive_child_sk = _derive_path(self.root_private_key, path)
```

It then, for every `SigningTarget` whose fingerprint matches a derived (or sum-hinted) key, signs the caller-supplied `target.message` verbatim: [2](#0-1) 

```
elif pk_fingerprint in sk_lookup:
    responses.append(
        SigningResponse(
            bytes(AugSchemeMPL.sign(sk_lookup[pk_fingerprint], target.message)),
            target.hook,
        )
    )
```

There is no check that:
- the derivation path corresponds to an address the wallet has actually used or derived through its normal puzzle-store/derivation-index bookkeeping,
- the `message` is the tree-hash/coin-name/`AGG_SIG_ME_ADDITIONAL_DATA` construction produced by a real `CoinSpend` the wallet itself is spending (compare with the legitimate flow in `WalletSigner.gather_signing_info()`, [3](#0-2) , which derives messages only from real `pkm_pairs_for_conditions_dict()` output of an actual spend),
- the caller is authorized to obtain a signature for that particular coin/purpose at all.

This function is exposed directly by the wallet RPC endpoint with no additional gating: [4](#0-3) 

```
async def execute_signing_instructions(
    self,
    request: ExecuteSigningInstructions,
) -> ExecuteSigningInstructionsResponse:
    return ExecuteSigningInstructionsResponse(
        signing_responses=await self.service.wallet_state_manager.signer.execute_signing_instructions(
            request.signing_instructions, request.partial_allowed
        )
    )
```

and registered in the metadata table with no special authorization decorator beyond the normal RPC transport: [5](#0-4) .

Because BLS AugSchemeMPL signatures are produced directly over the raw message bytes supplied by the caller (or `AugSchemeMPL.sign(sk, target.message, pk_lookup[pk_fingerprint])` for sum-hinted keys), this endpoint functions as a universal signing oracle across the wallet's entire HD keyspace: any caller who can invoke this RPC can request a signature under any derivation path from the wallet's root key, over any message of their choosing — including the exact `delegated_puzzle_hash + coin_name + AGG_SIG_ME_ADDITIONAL_DATA` byte string that constitutes a valid `AGG_SIG_ME` authorization for spending a real, currently-owned coin controlled by that derived key, entirely bypassing `WalletActionScope`, coin selection, `WalletStateManager.lock`, and all the normal transaction-construction/authorization plumbing described for wallet RPC transaction endpoints ( [6](#0-5) ).

### Impact Explanation
An unprivileged caller of the wallet RPC (the task's "local RPC caller" surface) can obtain valid BLS signatures for arbitrary messages under arbitrary keys derived from the wallet's master private key. Since standard Chia p2_delegated_puzzle_or_hidden_puzzle spends are authorized purely by an `AGG_SIG_ME` signature over `puzzle_hash(delegated_puzzle) + coin_name + AGG_SIG_ME_ADDITIONAL_DATA`, an attacker who can compute a target coin's id and the wallet's synthetic public key for that coin can request exactly this message be signed via `execute_signing_instructions`, then assemble a `CoinSpend`/`SpendBundle` themselves and push it to the mempool — moving/stealing coins without ever going through the wallet's own transaction-construction, coin-selection, or push logic. This is a concrete unauthorized/forged coin-movement primitive (signature-oracle theft), matching the "unsigned or unauthorized coin movement" acceptance criterion.

### Likelihood Explanation
The RPC is a documented, first-class part of the wallet API (signer protocol, used for hardware/offline signer flows) and is dispatched with no additional per-target validation. Any process capable of reaching the wallet RPC (local admin surface, but explicitly within the analog scope as "local RPC caller") can drive it with attacker-chosen `PathHint`/`SigningTarget` values. The only work required is computing the coin id and puzzle hash for a target coin, which are public/derivable, and the additional-data constant, which is a known consensus constant. No cryptographic primitive is broken — this is a design/authorization gap, not a crypto break.

### Recommendation
`execute_signing_instructions` (and the underlying `WalletSigner.execute_signing_instructions()`) must not blindly sign caller-supplied byte strings. At minimum:
- Validate that every `SigningTarget.message` corresponds to a message actually produced by `gather_signing_info()`/`pkm_pairs_for_conditions_dict()` for a `CoinSpend` that the caller has also supplied and that the wallet recognizes/owns (matching the existing safe path used for normal transaction endpoints).
- Restrict `PathHint.path` derivation to indices already tracked in `WalletPuzzleStore`'s derivation records rather than deriving arbitrary/unused paths from the root key on demand.
- Treat this endpoint as a privileged/administrative operation with explicit authorization separate from ordinary read-only wallet RPC calls, and document/limit it to trusted offline-signer tooling only.

### Proof of Concept
1. Query the wallet RPC for a target coin (or compute it) owned by address `A`, derived from pubkey `pk` at path `[12381, 8444, 2, i]`.
2. Compute `synthetic_pubkey` for `pk` and `message = delegated_puzzle_hash(ACS or attacker-chosen delegated puzzle) + coin.name() + AGG_SIG_ME_ADDITIONAL_DATA`.
3. Call `execute_signing_instructions` with:
   - `KeyHints.path_hints = [PathHint(root_fingerprint, [12381, 8444, 2, i])]`
   - `targets = [SigningTarget(synthetic_pubkey_fingerprint, message, hook)]`
   as shown being accepted in [7](#0-6)  (this test demonstrates exactly this "path hint only" flow producing a valid signature for an arbitrary caller-chosen `test_name` message).
4. Use the returned signature to build a `CoinSpend`/`WalletSpendBundle` for the target coin with the attacker's own delegated puzzle/solution and push it to the mempool directly (bypassing `WalletActionScope`/wallet RPC transaction endpoints entirely).

### Citations

**File:** chia/wallet/wallet_signer.py (L94-117)
```python
    async def gather_signing_info(self, spends: list[Spend]) -> SigningInstructions:
        pks: list[bytes] = []
        signing_targets: list[SigningTarget] = []
        for spend in spends:
            coin_spend = spend.as_coin_spend()
            # Get AGG_SIG conditions
            conditions_dict = conditions_dict_for_solution(
                coin_spend.puzzle_reveal,
                coin_spend.solution,
                self.max_block_cost_clvm,
            )
            # Create signature
            for pk, msg in pkm_pairs_for_conditions_dict(
                conditions_dict, coin_spend.coin, self.agg_sig_me_additional_data
            ):
                pk_bytes = bytes(pk)
                pks.append(pk_bytes)
                fingerprint: bytes = pk.get_fingerprint().to_bytes(4, "big")
                signing_targets.append(SigningTarget(fingerprint, msg, std_hash(pk_bytes + msg)))

        return SigningInstructions(
            await self.key_hints_for_pubkeys(pks),
            signing_targets,
        )
```

**File:** chia/wallet/wallet_signer.py (L140-170)
```python
    async def execute_signing_instructions(
        self, signing_instructions: SigningInstructions, partial_allowed: bool = False
    ) -> list[SigningResponse]:
        pk_lookup: dict[int, G1Element] = (
            {self.root_pubkey.get_fingerprint(): self.root_pubkey} if self.root_private_key is not None else {}
        )
        sk_lookup: dict[int, PrivateKey] = (
            {self.root_pubkey.get_fingerprint(): self.root_private_key} if self.root_private_key is not None else {}
        )
        aggregate_responses_at_end: bool = True
        responses: list[SigningResponse] = []

        # TODO: expand path hints and sum hints recursively (a sum hint can give a new key to path hint)
        # Next, expand our pubkey set with path hints
        if self.root_private_key is not None:
            for path_hint in signing_instructions.key_hints.path_hints:
                if int.from_bytes(path_hint.root_fingerprint, "big") != self.root_pubkey.get_fingerprint():
                    if not partial_allowed:
                        raise ValueError(f"No root pubkey for fingerprint {self.root_pubkey.get_fingerprint()}")
                    else:
                        continue
                else:
                    path = [int(step) for step in path_hint.path]
                    derive_child_sk = _derive_path(self.root_private_key, path)
                    derive_child_sk_unhardened = _derive_path_unhardened(self.root_private_key, path)
                    derive_child_pk = derive_child_sk.get_g1()
                    derive_child_pk_unhardened = derive_child_sk_unhardened.get_g1()
                    pk_lookup[derive_child_pk.get_fingerprint()] = derive_child_pk
                    pk_lookup[derive_child_pk_unhardened.get_fingerprint()] = derive_child_pk_unhardened
                    sk_lookup[derive_child_pk.get_fingerprint()] = derive_child_sk
                    sk_lookup[derive_child_pk_unhardened.get_fingerprint()] = derive_child_sk_unhardened
```

**File:** chia/wallet/wallet_signer.py (L206-212)
```python
            elif pk_fingerprint in sk_lookup:
                responses.append(
                    SigningResponse(
                        bytes(AugSchemeMPL.sign(sk_lookup[pk_fingerprint], target.message)),
                        target.hook,
                    )
                )
```

**File:** chia/wallet/wallet_rpc_api.py (L3513-3521)
```python
    async def execute_signing_instructions(
        self,
        request: ExecuteSigningInstructions,
    ) -> ExecuteSigningInstructionsResponse:
        return ExecuteSigningInstructionsResponse(
            signing_responses=await self.service.wallet_state_manager.signer.execute_signing_instructions(
                request.signing_instructions, request.partial_allowed
            )
        )
```

**File:** chia/wallet/wallet_rpc_metadata.py (L727-731)
```python
    WalletRpcMetadata(
        endpoint_name="execute_signing_instructions",
        request_type=wallet_request_types.ExecuteSigningInstructions,
        response_type=wallet_request_types.ExecuteSigningInstructionsResponse,
    ),
```

**File:** .cursor/context/wallet.md (L28-29)
```markdown
- `WalletActionScope` is the transaction side-effect boundary. Spend builders stage transactions, signing responses, extra spends, singleton records, selected coins, and unused derivation records; persistence/signing/push behavior happens after the scope exits through `add_pending_transactions()`.
- Wallet RPC transaction endpoints are not thin method calls. `tx_endpoint()` gates sync/connectivity, autofills `TXConfig`, folds in extra conditions and absolute timelocks, rejects relative timelocks, opens the action scope, and then normalizes signing/push response metadata.
```

**File:** chia/_tests/wallet/test_signer_protocol.py (L322-334)
```python
    # Test just a path hint
    test_name: bytes32 = std_hash(b"path hint only")
    child_sk: PrivateKey = _derive_path_unhardened(root_sk, [uint64(1), uint64(2), uint64(3), uint64(4)])
    signing_responses: list[SigningResponse] = await wallet.wallet_state_manager.signer.execute_signing_instructions(
        SigningInstructions(
            KeyHints(
                [],
                [PathHint(root_fingerprint, [uint64(1), uint64(2), uint64(3), uint64(4)])],
            ),
            [SigningTarget(child_sk.get_g1().get_fingerprint().to_bytes(4, "big"), test_name, test_name)],
        )
    )
    assert signing_responses == [SigningResponse(bytes(AugSchemeMPL.sign(child_sk, test_name)), test_name)]
```
