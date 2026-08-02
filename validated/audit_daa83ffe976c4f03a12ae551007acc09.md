## Finding: Stale `StagingArea` chunks silently corrupt the next chunked-publish session

Cite: [1](#0-0) , [2](#0-1) , [3](#0-2) 

### Title
Un-cleared `StagingArea.code` entries are silently appended-to/reused across chunked-publish sessions, letting stale (or attacker-crafted) module bytes be smuggled into a later package's committed code — ([File: aptos-move/framework/aptos-experimental/sources/large_packages.move])

### Summary
`aptos_experimental::large_packages` implements chunked publishing: callers repeatedly invoke `stage_code_chunk` to accumulate module bytes indexed by `code_indices` into a per-account `StagingArea`, then finalize with `stage_code_chunk_and_publish_to_account`/`_to_object`/`_and_upgrade_object_code`, which call `assemble_module_code` and hand the result to `code::publish_package_txn`/`object_code_deployment`. `stage_code_chunk_internal` never resets or validates that the `StagingArea` is "fresh" for a new package: if an index already exists in the `SmartTable`, new bytes are **appended** to whatever is already stored there instead of being validated against a clean state, and `last_module_idx` only ever grows (it is never lowered) across sessions. The only mitigation is the CLI printing a warning and offering `clear-staging-area`/`cleanup_staging_area` — this is purely a client-side convention, not an on-chain invariant.

### Finding Description
`stage_code_chunk_internal` ( [2](#0-1) ):
```
if (staging_area.code.contains(idx)) {
    staging_area.code.borrow_mut(idx).append(inner_code);
} else {
    staging_area.code.add(idx, inner_code);
    if (idx > staging_area.last_module_idx) {
        staging_area.last_module_idx = idx;
    }
};
```
- If a previous staging session for the same owner address was abandoned (aborted transaction, ran out of gas, wrong indices, or simply forgotten) without calling `cleanup_staging_area`, its `code` table and `last_module_idx` persist under the `StagingArea` resource.
- A subsequent, unrelated staging session for a *different* package reusing the same index values does not overwrite — it **concatenates** new bytes onto the stale leftover bytes at that index.
- `last_module_idx` is monotonically increasing and is never reset by a new session, so `assemble_module_code` ( [3](#0-2) ) will iterate `0..=last_module_idx` and include **every** leftover index from the earlier abandoned package, even ones never touched by the current session, alongside the corrupted (concatenated) entries.
- The assembled `code: vector<vector<u8>>` is then passed to `code::publish_package_txn`/`object_code_deployment::publish`/`upgrade` together with the *current* session's `PackageMetadata`. There is no on-chain check inside `large_packages.move` that the number/identity of assembled code blobs actually corresponds to what the caller intended for this session — that reconciliation only happens indirectly, deep inside the native `request_publish`/`request_publish_with_allowed_deps` bytecode-vs-metadata check in `code.move` ( [4](#0-3) ).
- The Aptos CLI is aware this is dangerous and explicitly warns the operator and offers cleanup ( [5](#0-4) ), which confirms this is a known operational hazard that is **not** enforced by the Move module itself — any caller/integration that does not go through the CLI's warning/cleanup flow (SDKs, bots, or a shared multi-tenant publishing service using one signer for many users' packages) has no protection.

### Impact Explanation
This creates a genuine mismatch between what a caller intends to publish (declared in the current session's `PackageMetadata`) and what bytecode actually ends up bundled into the `code` vector passed to `code::publish_package_txn`/`object_code_deployment`, matching the "mismatch between verified bytes, package metadata... and committed module bytes" publish-safety invariant. Concretely:
- In a shared/multi-tenant deployment pipeline (a service that stages and publishes packages on behalf of multiple unrelated users from one signer/resource account — the documented use case for this "framework" module at a well-known mainnet address), a failed/partial staging left by one user's package can silently pollute the next unrelated package's published bytecode with residual module bytes, or corrupt a module's bytes by concatenation, producing bytecode that does not match the intended source.
- Best case this causes the publish transaction to abort (index/count mismatch caught by the verifier/native layer) — a denial-of-service on legitimate publishing for that account until `cleanup_staging_area` is explicitly called.
- Worst case (index counts happen to align, e.g., an attacker deliberately pre-seeds indices to match a victim service's expected module count on a shared staging address) it allows extra/stale bytecode to be committed as part of a package without that code being reflected in, or verifiable against, the metadata the publisher believed they were submitting — a code-safety/ownership violation of the "verified bytes match published bytes" guarantee that `code::publish_package`/`VerifyPackage` otherwise relies on.

### Likelihood Explanation
Requires either (a) an operator/integration that doesn't follow the CLI's cleanup discipline (plausible for direct SDK/bot usage, since the cleanup step is advisory, not enforced on-chain) hitting a failed/partial staging transaction and then reusing the same account for a different package, or (b) a shared staging signer used by a multi-tenant publish service. Both are realistic given `large_packages.move` is explicitly documented as reusable "framework" infrastructure deployed at a fixed mainnet address for third-party SDK/tooling use, not a single-user CLI-only flow.

### Recommendation
- Require `StagingArea` to be empty (or auto-clear it) at the start of a new staging sequence, or bind a `StagingArea` to a package identity/session nonce so leftover entries from a different session can never be silently reused/appended to.
- On finalize, validate that `code_indices` supplied across the whole session form a contiguous `0..N-1` range with no pre-existing entries from a prior, un-cleaned session (or simply disallow `stage_code_chunk_internal` from appending to a pre-existing index unless it was written earlier in the *same* logical session).
- Enforce this invariant on-chain in `large_packages.move` rather than relying on off-chain CLI warnings.

### Proof of Concept
1. Owner `O` starts staging Package X (10 modules) via `stage_code_chunk` for `code_indices = [0..5]`, then the transaction sequence is abandoned (e.g., insufficient gas budgeted for the remaining chunks, or the client crashes) — `StagingArea.code` now holds indices `0..5`, `last_module_idx = 5`, without `cleanup_staging_area` ever being called.
2. `O` (or a shared service acting for a different, unrelated package Y with 3 modules) later calls `stage_code_chunk_and_publish_to_account` with `code_indices = [0,1,2]` intending to publish only Package Y's 3 modules.
3. `stage_code_chunk_internal` appends Package Y's module bytes onto the *existing* Package X bytes at indices 0–2 (corrupting them via concatenation), and leaves indices 3–5 as pure Package X leftovers untouched by this session.
4. `assemble_module_code` iterates `0..=5` (since `last_module_idx` was never reset) and returns 6 code blobs — 3 corrupted/concatenated blobs and 3 fully-stale Package X blobs — which are submitted together with Package Y's metadata (which only declares 3 modules) to `code::publish_package_txn`.
5. Depending on how the native module-count/name check reacts, this either aborts (self-DoS requiring `cleanup_staging_area`) or, if index/count happens to line up in a crafted attack against a shared staging account, results in bytecode being committed to chain that does not match the metadata the victim session declared.

### Uncertainty
I was not able to load `object_code_deployment.move` or the native `request_publish`/`request_publish_with_allowed_deps` implementation in this pass to confirm exactly whether a module-count mismatch between `PackageMetadata.modules` and the `code` vector length is strictly enforced before storage mutation, or whether it's possible for extra/stale blobs to be silently ignored/stored. That native-layer check is the deciding factor for whether this reaches "high/critical" (on-chain code corruption) versus "abort/DoS only." A Devin session with full repo access should inspect `aptos-move/framework/natives/src/code.rs` (or equivalent) to confirm the exact enforcement before treating this as more than a DoS-level finding.

### Citations

**File:** aptos-move/framework/aptos-experimental/sources/large_packages.move (L59-63)
```text
    struct StagingArea has key {
        metadata_serialized: vector<u8>,
        code: SmartTable<u64, vector<u8>>,
        last_module_idx: u64
    }
```

**File:** aptos-move/framework/aptos-experimental/sources/large_packages.move (L131-178)
```text
    inline fun stage_code_chunk_internal(
        owner: &signer,
        metadata_chunk: vector<u8>,
        code_indices: vector<u16>,
        code_chunks: vector<vector<u8>>
    ): &mut StagingArea {
        assert!(
            code_indices.length() == code_chunks.length(),
            error::invalid_argument(ECODE_MISMATCH)
        );

        let owner_address = signer::address_of(owner);

        if (!exists<StagingArea>(owner_address)) {
            move_to(
                owner,
                StagingArea {
                    metadata_serialized: vector[],
                    code: smart_table::new(),
                    last_module_idx: 0
                }
            );
        };

        let staging_area = borrow_global_mut<StagingArea>(owner_address);

        if (!metadata_chunk.is_empty()) {
            staging_area.metadata_serialized.append(metadata_chunk);
        };

        let i = 0;
        while (i < code_chunks.length()) {
            let inner_code = code_chunks[i];
            let idx = (code_indices[i] as u64);

            if (staging_area.code.contains(idx)) {
                staging_area.code.borrow_mut(idx).append(inner_code);
            } else {
                staging_area.code.add(idx, inner_code);
                if (idx > staging_area.last_module_idx) {
                    staging_area.last_module_idx = idx;
                }
            };
            i += 1;
        };

        staging_area
    }
```

**File:** aptos-move/framework/aptos-experimental/sources/large_packages.move (L210-219)
```text
    inline fun assemble_module_code(staging_area: &mut StagingArea): vector<vector<u8>> {
        let last_module_idx = staging_area.last_module_idx;
        let code = vector[];
        let i = 0;
        while (i <= last_module_idx) {
            code.push_back(*staging_area.code.borrow(i));
            i += 1;
        };
        code
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L224-231)
```text
        // Request publish
        if (features::code_dependency_check_enabled())
            request_publish_with_allowed_deps(addr, module_names, allowed_deps, code, policy.policy)
        else
        // The new `request_publish_with_allowed_deps` has not yet rolled out, so call downwards
        // compatible code.
            request_publish(addr, module_names, code, policy.policy)
    }
```

**File:** aptos-move/cli/src/commands.rs (L1753-1763)
```rust
    if !is_staging_area_empty(txn_options, large_packages_module_address).await? {
        let message = format!(
            "The resource {}::large_packages::StagingArea under account {} is not empty.\
        \nThis may cause package publishing to fail if the data is unexpected. \
        \nUse the `aptos move clear-staging-area` command to clean up the `StagingArea` resource under the account.",
            large_packages_module_address, account_address,
        )
            .bold();
        println!("{}", message);
        prompt_yes_with_override("Do you want to proceed?", txn_options.prompt_options)?;
    }
```
