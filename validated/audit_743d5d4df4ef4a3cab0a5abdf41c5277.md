The keychain daemon RPC surface exposes destructive key-management operations (`delete_all_keys`, `delete_key_by_fingerprint`) that are gated only by whether the keyring is *unlocked*, not by re-supplying the master passphrase for the specific mutating call — this is the same bug class as CVE-2018-1286 (CRUD on privileged identities lacking password protection, enabling an authenticated caller to DoS privileged users).

### Title
Unlocked-keyring CRUD RPCs allow any local daemon/RPC caller to wipe all keychain keys without re-authentication - (File: chia/daemon/keychain_server.py)

### Summary
`KeychainServer.delete_all_keys()` and `KeychainServer.delete_key_by_fingerprint()` only check `is_keyring_locked()` before performing destructive operations; neither requires the caller to supply the master passphrase for that specific request. [1](#0-0)  Once the keyring is unlocked (e.g., after a wallet/farmer service does normal startup unlock), any client able to reach the daemon keychain RPC boundary or the Wallet RPC `delete_all_keys`/`delete_key` endpoints can permanently erase every private key in the keychain with a single unauthenticated-for-that-action call. [2](#0-1) 

### Finding Description
The daemon's `KeychainServer` intercepts `keychain_commands` (including `delete_all_keys`, `delete_key_by_fingerprint`) and dispatches to handlers that gate on lock state only: `if self.get_keychain_for_request(request).is_keyring_locked(): return {"success": False, ...}` — otherwise the deletion proceeds immediately. [3](#0-2)  `Keychain.delete_all_keys()` iterates through every stored key and removes it unconditionally. [4](#0-3) 

The Wallet RPC layer forwards directly to this without additional passphrase confirmation: `WalletRpcApi.delete_all_keys()` calls `self.service.keychain_proxy.delete_all_keys()` and then deletes on-disk wallet DBs for every fingerprint, with no requirement to re-enter the master passphrase in the request payload. [2](#0-1)  Similarly `delete_key` deletes an individual key by fingerprint and its wallet DB with no extra confirmation. [5](#0-4) 

Contrast this with the truly sensitive keyring operations — `set_keyring_passphrase` and `remove_keyring_passphrase` — which do require `current_passphrase` to be supplied and validated before mutating keyring passphrase state. [6](#0-5)  Key deletion operations, despite being just as destructive to the privileged identities (farmer/pool/wallet master keys) that the passphrase is meant to protect, skip this per-action confirmation and rely solely on the ambient "keyring unlocked" state, which can persist across many unrelated RPC calls once cached. [7](#0-6) 

This mirrors CVE-2018-1286 exactly: CRUD operations on privileged accounts/identities are not protected by re-authentication, so any caller already holding baseline RPC access (an "authenticated attacker" in the advisory's terms) can destroy privileged state and deny service to legitimate privileged users (farmer, pool, wallet owners) who rely on those keys.

### Impact Explanation
A caller with only baseline access to the local daemon keychain RPC or the Wallet RPC (e.g., a co-located but lower-privileged process, a compromised local application with wallet-RPC access, or any script relying on cached RPC credentials) can call `delete_all_keys` while the keyring happens to be unlocked (a very common runtime state for farmer/harvester/wallet services) and instantly and irreversibly wipe every master key. This halts all transaction signing/spending, farming, and pooling operations tied to those keys — a direct denial of service against privileged users, matching the "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
The keyring is unlocked by default for most running node/farmer/wallet deployments (`is_keyring_locked()` returns `False` whenever no master passphrase is set, which is the default configuration for most users). [8](#0-7)  Any caller that already has network/local access sufficient to send daemon or Wallet RPC requests (the same trust level required to call any other RPC method) can trigger this with a single well-formed request, requiring no special privilege beyond generic RPC reachability.

### Recommendation
Require re-entry and validation of the master passphrase (or an equivalent step-up confirmation) as part of the `delete_all_keys` and `delete_key_by_fingerprint` request payloads, similar to how `set_keyring_passphrase`/`remove_keyring_passphrase` already require `current_passphrase`. Consider adding a confirmation flag/second-factor for irreversible bulk-deletion RPCs and audit-log such operations.

### Proof of Concept
1. Start a chia daemon and wallet service in default configuration (no master passphrase set, so `Keychain.is_keyring_locked()` is `False`).
2. From any process capable of reaching the Wallet RPC endpoint (or the daemon keychain RPC), send `delete_all_keys` with no passphrase parameter: `await client.delete_all_keys()`. [9](#0-8) 
3. Observe that all keys and wallet DB files are deleted immediately, denying service to the farmer/pool/wallet operators who owned those keys, with no passphrase or additional confirmation ever requested.

### Citations

**File:** chia/daemon/keychain_server.py (L264-286)
```python
    async def delete_all_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        self.get_keychain_for_request(request).delete_all_keys()

        return {"success": True}

    async def delete_key_by_fingerprint(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        fingerprint = request.get("fingerprint", None)
        if fingerprint is None:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": "missing fingerprint"},
            }

        self.get_keychain_for_request(request).delete_key_by_fingerprint(fingerprint)

        return {"success": True}
```

**File:** chia/wallet/wallet_rpc_api.py (L726-740)
```python
    async def delete_key(self, request: DeleteKey) -> Empty:
        await self._stop_wallet()
        try:
            await self.service.keychain_proxy.delete_key_by_fingerprint(request.fingerprint)
        except Exception as e:
            log.error(f"Failed to delete key by fingerprint: {e}")
            raise e
        path = get_wallet_db_path(
            self.service.root_path,
            self.service.config,
            str(request.fingerprint),
        )
        if path.exists():
            path.unlink()
        return Empty()
```

**File:** chia/wallet/wallet_rpc_api.py (L820-836)
```python
    async def delete_all_keys(self, request: Empty) -> Empty:
        await self._stop_wallet()
        all_key_datas = await self.service.keychain_proxy.get_keys()
        try:
            await self.service.keychain_proxy.delete_all_keys()
        except Exception as e:
            log.error(f"Failed to delete all keys: {e}")
            raise e
        for key_data in all_key_datas:
            path = get_wallet_db_path(
                self.service.root_path,
                self.service.config,
                str(key_data.fingerprint),
            )
            if path.exists():
                path.unlink()
        return Empty()
```

**File:** chia/util/keychain.py (L537-542)
```python
    def delete_all_keys(self) -> None:
        """
        Deletes all keys from the keychain.
        """
        for key_data in self._iterate_through_key_datas(include_secrets=False, skip_public_only=False):
            self.delete_key_by_fingerprint(key_data.fingerprint)
```

**File:** chia/util/keychain.py (L544-562)
```python
    @staticmethod
    def is_keyring_locked() -> bool:
        """
        Returns whether the keyring is in a locked state. If the keyring doesn't have a master passphrase set,
        or if a master passphrase is set and the cached passphrase is valid, the keyring is "unlocked"
        """
        # Unlocked: If a master passphrase isn't set, or if the cached passphrase is valid
        if not Keychain.has_master_passphrase():
            return False

        passphrase = Keychain.get_cached_master_passphrase()
        if passphrase is None:
            return True

        if Keychain.master_passphrase_is_valid(passphrase):
            return False

        # Locked: Everything else
        return True
```

**File:** chia/daemon/server.py (L572-641)
```python
    async def set_keyring_passphrase(self, websocket: WebSocketResponse, request: dict[str, Any]) -> dict[str, Any]:
        success: bool = False
        error: str | None = None
        current_passphrase: str | None = None
        new_passphrase: str | None = None
        passphrase_hint: str | None = request.get("passphrase_hint", None)
        save_passphrase: bool = request.get("save_passphrase", False)

        if using_default_passphrase():
            current_passphrase = default_passphrase()

        if Keychain.has_master_passphrase() and not current_passphrase:
            current_passphrase = request.get("current_passphrase", None)
            if type(current_passphrase) is not str:
                return {"success": False, "error": "missing current_passphrase"}

        new_passphrase = request.get("new_passphrase", None)
        if type(new_passphrase) is not str:
            return {"success": False, "error": "missing new_passphrase"}

        if not Keychain.passphrase_meets_requirements(new_passphrase):
            return {"success": False, "error": "passphrase doesn't satisfy requirements"}

        try:
            assert new_passphrase is not None  # mypy, I love you
            Keychain.set_master_passphrase(
                current_passphrase,
                new_passphrase,
                passphrase_hint=passphrase_hint,
                save_passphrase=save_passphrase,
            )
        except KeychainCurrentPassphraseIsInvalid:
            error = "current passphrase is invalid"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Failed to set keyring passphrase: {e} {tb}")
        else:
            success = True
            # Inform the GUI of keyring status changes
            self.keyring_status_changed(await self.keyring_status(), "wallet_ui")

        response: dict[str, Any] = {"success": success, "error": error}
        return response

    async def remove_keyring_passphrase(self, websocket: WebSocketResponse, request: dict[str, Any]) -> dict[str, Any]:
        success: bool = False
        error: str | None = None
        current_passphrase: str | None = None

        if not Keychain.has_master_passphrase():
            return {"success": False, "error": "passphrase not set"}

        current_passphrase = request.get("current_passphrase", None)
        if type(current_passphrase) is not str:
            return {"success": False, "error": "missing current_passphrase"}

        try:
            Keychain.remove_master_passphrase(current_passphrase)
        except KeychainCurrentPassphraseIsInvalid:
            error = "current passphrase is invalid"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Failed to remove keyring passphrase: {e} {tb}")
        else:
            success = True
            # Inform the GUI of keyring status changes
            self.keyring_status_changed(await self.keyring_status(), "wallet_ui")

        response: dict[str, Any] = {"success": success, "error": error}
        return response
```

**File:** chia/util/keyring_wrapper.py (L85-99)
```python
    if use_passphrase_cache:
        passphrase, validated = KeyringWrapper.get_shared_instance().get_cached_master_passphrase()
        if passphrase:
            # If the cached passphrase was previously validated, we assume it's... valid
            if validated:
                return passphrase

            # Cached passphrase needs to be validated
            if KeyringWrapper.get_shared_instance().master_passphrase_is_valid(passphrase):
                KeyringWrapper.get_shared_instance().set_cached_master_passphrase(passphrase, validated=True)
                return passphrase
            else:
                # Cached passphrase is bad, clear the cache
                KeyringWrapper.get_shared_instance().set_cached_master_passphrase(None)

```
