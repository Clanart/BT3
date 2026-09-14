### Title
`add_vc_authorization` selects the first matching Verified Credential without checking proof sufficiency, causing offer-take spend failure - (File: `chia/wallet/vc_wallet/vc_wallet.py`)

### Summary
When a wallet approves CR-CAT spends embedded in an offer, `VCWallet.add_vc_authorization()` picks `available_vcs[0]` — the first VC record returned for the CR-CAT's `authorized_providers` — instead of the VC whose proofs actually satisfy the CR-CAT's `proofs_checker` flags. This mirrors the GoGoPool `requireNextActiveMultisig` bug class: a function that should pick a *suitable* item among several candidates instead deterministically/arbitrarily returns "the first one," which can select an unusable credential and get the operation stuck, even though a valid alternative exists in the same wallet.

### Finding Description
`VCWallet.add_vc_authorization()` is the taker/offer-authorization path used whenever an offer contains CR-CAT coins that need a Verified Credential (VC) proof of inclusion to complete the spend: [1](#0-0) 

For each CR-CAT spend needing approval, the code queries `VCStore.get_vc_records_by_providers()`, which has no `ORDER BY` clause and simply returns whatever the DB gives back: [2](#0-1) 

`add_vc_authorization` then unconditionally uses `available_vcs[0].vc` for this CR-CAT, without verifying that this specific VC's `proof_hash` actually contains the proof keys required by the CR-CAT's `proofs_checker`. The chosen VC's `proof_hash` is later passed straight into `proof_of_inclusions_for_root_and_keys`, which calls `VCProofs.prove_keys(keys)`: [3](#0-2) 

This contrasts with the "correct" pattern used elsewhere in the same file and in `CRCATWallet`, `get_vc_with_provider_in_and_proofs()`, which iterates over all matching VC records and only returns one whose proofs actually satisfy the required flags: [4](#0-3) 

If a wallet owns multiple VCs from providers in the CR-CAT's `authorized_providers` list (a normal situation for a wallet that interacts with several credential-restricted assets or has re-issued/updated proofs on a VC), and the arbitrarily-first-returned VC does not carry the specific proof flags the CR-CAT's `proofs_checker` requires, `add_vc_authorization` will still select it (`available_vcs[0]`) instead of falling back to the wallet's other, valid VC.

### Impact Explanation
Because the wrong (first) VC is bound into the authorization solver data unconditionally, `prove_keys()`/downstream proof construction is expected to fail for the required-but-missing proof flags, aborting the offer-take flow for a CR-CAT spend that the wallet could otherwise have validly completed with a different VC it already owns. This is a spend-triggered transaction-processing halt on the offer/CR-CAT approval path: an otherwise legitimate offer take by an unprivileged wallet user is deterministically blocked whenever the "first" VC record returned by the DB query happens not to be the correct one — a probability that only increases as a user accumulates more VCs, directly analogous to how `requireNextActiveMultisig` always returning the first (possibly wrong/disabled) multisig increases the probability of stuck minipools.

### Likelihood Explanation
Likelihood is moderate: it requires a wallet to hold more than one VC whose `proof_provider` matches a CR-CAT's `authorized_providers` list, with the DB-returned ordering placing an insufficiently-proofed VC first. This is plausible for active DataLayer/CR-CAT/VC users managing several credentials or credentials with different proof sets, and does not require any malicious counterparty — only normal offer-taking activity with credential-restricted CATs.

### Recommendation
In `add_vc_authorization`, mirror the pattern already used by `get_vc_with_provider_in_and_proofs`: iterate over `available_vcs`, and select the first VC whose `proof_hash`/`VCProofs` actually contains all proof keys required by `crcat_spend.crcat.proofs_checker` (analogous to checking `all(proof in vc_proofs.key_value_pairs for proof in proofs)`), only raising `ValueError` if no such suitable VC exists. This avoids deterministically failing offer-take operations when a suitable VC is available elsewhere in the same list.

### Proof of Concept
Conceptual reproduction (would need to be validated in a running Devin session with actual wallet/VC/CR-CAT fixtures):
1. Mint two VCs (`vc_A`, `vc_B`) from the same DID/provider whose `launcher_id`/`proof_provider` is in a CR-CAT asset's `authorized_providers`.
2. Add proofs to `vc_B` that satisfy the CR-CAT's `proofs_checker` flags (e.g., a specific KYC flag), but leave `vc_A` with no/insufficient proofs.
3. Ensure the DB query in `get_vc_records_by_providers` (no explicit ordering) returns `vc_A` before `vc_B` (e.g., by minting/confirming `vc_A` first, matching typical rowid ordering).
4. Take an offer that includes a CR-CAT spend requiring the proof flags satisfied only by `vc_B`.
5. Observe that `add_vc_authorization` binds `available_vcs[0].vc == vc_A` and the subsequent `proof_of_inclusions_for_root_and_keys` call using `vc_A.proof_hash` fails to produce the required proof, aborting the offer take — despite `vc_B` being available and valid in the same wallet.

### Citations

**File:** chia/wallet/vc_wallet/vc_wallet.py (L475-486)
```python
        for crcat_spend in crcat_spends:
            # Check first whether we can approve...
            available_vcs: list[VCRecord] = [
                vc_rec
                for vc_rec in await self.store.get_vc_records_by_providers(crcat_spend.crcat.authorized_providers)
                if vc_rec.confirmed_at_height != 0
            ]
            if len(available_vcs) == 0:  # pragma: no cover
                raise ValueError(f"No VC available with provider in {crcat_spend.crcat.authorized_providers}")
            vc: VerifiedCredential = available_vcs[0].vc
            vc_to_use: bytes32 = vc.launcher_id
            vcs[vc_to_use] = vc
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L533-543)
```python
                coin_name: str = crcat_spend.crcat.coin.name().hex()
                coin_args[coin_name] = (
                    await self.proof_of_inclusions_for_root_and_keys(
                        # It's on my TODO list to fix the below line -Quex
                        vc.proof_hash,  # type: ignore
                        ProofsChecker.from_program(UnknownPuzzle(known_program=crcat_spend.crcat.proofs_checker)).flags,
                    ),
                    vc.proof_provider,
                    vc.launcher_id,
                    vc.wrap_inner_with_backdoor().get_tree_hash(),
                )
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L610-625)
```python
    async def get_vc_with_provider_in_and_proofs(
        self, authorized_providers: list[bytes32], proofs: list[str]
    ) -> VerifiedCredential:
        vc_records: list[VCRecord] = await self.store.get_vc_records_by_providers(authorized_providers)
        if len(vc_records) == 0:  # pragma: no cover
            raise ValueError(f"VCWallet has no VCs with providers in the following list: {authorized_providers}")
        else:
            for rec in vc_records:
                if rec.vc.proof_hash is None:
                    continue  # pragma: no cover
                vc_proofs: VCProofs | None = await self.store.get_proofs_for_root(rec.vc.proof_hash)
                if vc_proofs is None:
                    continue  # pragma: no cover
                if all(proof in vc_proofs.key_value_pairs for proof in proofs):
                    return rec.vc
        raise ValueError(f"No authorized VC has the correct proofs: {proofs}")  # pragma: no cover
```

**File:** chia/wallet/vc_wallet/vc_store.py (L191-204)
```python
    async def get_vc_records_by_providers(self, provider_ids: list[bytes32]) -> list[VCRecord]:  # pragma: no cover
        """
        Checks DB for VCs with a proof_provider in a specified list and returns them.
        """
        async with self.db_wrapper.reader_no_transaction() as conn:
            providers_param: str = ", ".join(["?"] * len(provider_ids))
            cursor = await conn.execute(
                f"SELECT * from vc_records WHERE proof_provider IN ({providers_param}) LIMIT 1000",
                tuple(id.hex() for id in provider_ids),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        return [_row_to_vc_record(row) for row in rows]
```
