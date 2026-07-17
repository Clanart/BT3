import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "starkware-libs/sequencer"
REPO_NAME = "sequencer"
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    "chain/chain/src/approval_verification.rs",
    "chain/chain/src/block_processing_utils.rs",
    "chain/chain/src/chain.rs",
    "chain/chain/src/chain_update.rs",
    "chain/chain/src/doomslug.rs",
    "chain/chain/src/lightclient.rs",
    "chain/chain/src/missing_chunks.rs",
    "chain/chain/src/orphan.rs",
    "chain/chain/src/pending.rs",
    "chain/chain/src/resharding/flat_storage_resharder.rs",
    "chain/chain/src/resharding/manager.rs",
    "chain/chain/src/resharding/migrations.rs",
    "chain/chain/src/resharding/resharding_actor.rs",
    "chain/chain/src/resharding/trie_state_resharder.rs",
    "chain/chain/src/runtime/mod.rs",
    "chain/chain/src/runtime/signer_overlay.rs",
    "chain/chain/src/runtime/trie_update_wrapper.rs",
    "chain/chain/src/sharding.rs",
    "chain/chain/src/signature_verification.rs",
    "chain/chain/src/spice/block_application.rs",
    "chain/chain/src/spice/chain.rs",
    "chain/chain/src/spice/chunk_application.rs",
    "chain/chain/src/spice/chunk_validation.rs",
    "chain/chain/src/spice/core.rs",
    "chain/chain/src/state_sync/adapter.rs",
    "chain/chain/src/state_sync/mod.rs",
    "chain/chain/src/state_sync/state_request_tracker.rs",
    "chain/chain/src/state_sync/utils.rs",
    "chain/chain/src/stateless_validation/chunk_endorsement.rs",
    "chain/chain/src/stateless_validation/chunk_validation.rs",
    "chain/chain/src/stateless_validation/processing_tracker.rs",
    "chain/chain/src/stateless_validation/state_witness.rs",
    "chain/chain/src/types.rs",
    "chain/chain/src/update_shard.rs",
    "chain/chain/src/validate.rs",
    "chain/chunks/src/chunk_cache.rs",
    "chain/chunks/src/client.rs",
    "chain/chunks/src/logic.rs",
    "chain/chunks/src/shards_manager_actor.rs",
    "chain/client/src/chunk_endorsement_handler.rs",
    "chain/client/src/chunk_inclusion_tracker.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/client/src/client.rs",
    "chain/client/src/client_actor.rs",
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/prepare_transactions.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/state_request_actor.rs",
    "chain/client/src/stateless_validation/chunk_endorsement.rs",
    "chain/client/src/stateless_validation/chunk_validation_actor.rs",
    "chain/client/src/stateless_validation/chunk_validator/mod.rs",
    "chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs",
    "chain/client/src/stateless_validation/partial_witness/encoding.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_deploys_tracker.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs",
    "chain/client/src/stateless_validation/shadow_validate.rs",
    "chain/client/src/stateless_validation/state_witness_producer.rs",
    "chain/client/src/stateless_validation/state_witness_tracker.rs",
    "chain/client/src/stateless_validation/validate.rs",
    "chain/client/src/sync/block.rs",
    "chain/client/src/sync/epoch.rs",
    "chain/client/src/sync/external.rs",
    "chain/client/src/sync/handler.rs",
    "chain/client/src/sync/header.rs",
    "chain/client/src/sync/state/chain_requests.rs",
    "chain/client/src/sync/state/downloader.rs",
    "chain/client/src/sync/state/mod.rs",
    "chain/client/src/sync/state/network.rs",
    "chain/client/src/sync/state/shard.rs",
    "chain/client/src/sync/state/task_tracker.rs",
    "chain/client/src/sync/state/util.rs",
    "chain/client/src/view_client_actor.rs",
    "chain/epoch-manager/src/epoch_info_aggregator.rs",
    "chain/epoch-manager/src/epoch_sync.rs",
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/shard_assignment/mod.rs",
    "chain/epoch-manager/src/shard_assignment/sticky_resharding.rs",
    "chain/epoch-manager/src/shard_tracker.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/epoch-manager/src/validator_stats.rs",
    "chain/jsonrpc/src/api/blocks.rs",
    "chain/jsonrpc/src/api/call_function.rs",
    "chain/jsonrpc/src/api/chunks.rs",
    "chain/jsonrpc/src/api/light_client.rs",
    "chain/jsonrpc/src/api/query.rs",
    "chain/jsonrpc/src/api/status.rs",
    "chain/jsonrpc/src/api/transactions.rs",
    "chain/jsonrpc/src/api/validator.rs",
    "chain/jsonrpc/src/api/view_access_key.rs",
    "chain/jsonrpc/src/api/view_account.rs",
    "chain/jsonrpc/src/api/view_code.rs",
    "chain/jsonrpc/src/api/view_state.rs",
    "chain/jsonrpc/src/sharded_rpc.rs",
    "chain/network/src/accounts_data/mod.rs",
    "chain/network/src/announce_accounts/mod.rs",
    "chain/network/src/client.rs",
    "chain/network/src/network_protocol/edge.rs",
    "chain/network/src/network_protocol/mod.rs",
    "chain/network/src/network_protocol/peer.rs",
    "chain/network/src/network_protocol/state_sync.rs",
    "chain/network/src/peer/peer_actor.rs",
    "chain/network/src/peer_manager/peer_manager_actor.rs",
    "chain/network/src/routing/edge.rs",
    "chain/network/src/routing/graph/mod.rs",
    "chain/network/src/shards_manager.rs",
    "chain/network/src/state_sync.rs",
    "chain/network/src/state_witness.rs",
    "chain/network/src/types.rs",
    "chain/pool/src/lib.rs",
    "chain/pool/src/types.rs",
    "core/crypto/src/hash.rs",
    "core/crypto/src/hash_domain.rs",
    "core/crypto/src/signature.rs",
    "core/crypto/src/signer.rs",
    "core/crypto/src/vrf.rs",
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/apply.rs",
    "core/primitives-core/src/gas.rs",
    "core/primitives-core/src/hash.rs",
    "core/primitives-core/src/serialize.rs",
    "core/primitives-core/src/trie_key.rs",
    "core/primitives-core/src/types.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/block.rs",
    "core/primitives/src/block_body.rs",
    "core/primitives/src/block_header.rs",
    "core/primitives/src/challenge.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/epoch_block_info.rs",
    "core/primitives/src/epoch_info.rs",
    "core/primitives/src/epoch_manager.rs",
    "core/primitives/src/epoch_sync.rs",
    "core/primitives/src/merkle.rs",
    "core/primitives/src/optimistic_block.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/reed_solomon.rs",
    "core/primitives/src/shard_layout/mod.rs",
    "core/primitives/src/shard_layout/v1.rs",
    "core/primitives/src/shard_layout/v2.rs",
    "core/primitives/src/shard_layout/v3.rs",
    "core/primitives/src/sharding.rs",
    "core/primitives/src/sharding/shard_chunk_header_inner.rs",
    "core/primitives/src/spice/chunk_endorsement.rs",
    "core/primitives/src/spice/partial_data.rs",
    "core/primitives/src/spice/state_witness.rs",
    "core/primitives/src/state.rs",
    "core/primitives/src/state_part.rs",
    "core/primitives/src/state_record.rs",
    "core/primitives/src/state_sync.rs",
    "core/primitives/src/stateless_validation/chunk_endorsement.rs",
    "core/primitives/src/stateless_validation/chunk_endorsements_bitmap.rs",
    "core/primitives/src/stateless_validation/contract_distribution.rs",
    "core/primitives/src/stateless_validation/partial_witness.rs",
    "core/primitives/src/stateless_validation/state_witness.rs",
    "core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs",
    "core/primitives/src/stateless_validation/validator_assignment.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives/src/trie_split.rs",
    "core/primitives/src/types.rs",
    "core/primitives/src/upgrade_schedule.rs",
    "core/primitives/src/validator_mandates/compute_price.rs",
    "core/primitives/src/validator_signer.rs",
    "core/store/src/adapter/chain_store.rs",
    "core/store/src/adapter/chunk_store.rs",
    "core/store/src/adapter/epoch_store.rs",
    "core/store/src/adapter/flat_store.rs",
    "core/store/src/adapter/trie_store.rs",
    "core/store/src/flat/delta.rs",
    "core/store/src/flat/manager.rs",
    "core/store/src/flat/storage.rs",
    "core/store/src/flat/types.rs",
    "core/store/src/merkle_proof.rs",
    "core/store/src/trie/from_flat.rs",
    "core/store/src/trie/iterator.rs",
    "core/store/src/trie/mem/loading.rs",
    "core/store/src/trie/mem/memtries.rs",
    "core/store/src/trie/mem/memtrie_update.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/interface.rs",
    "core/store/src/trie/ops/iter.rs",
    "core/store/src/trie/ops/resharding.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/shard_tries.rs",
    "core/store/src/trie/split.rs",
    "core/store/src/trie/state_parts.rs",
    "core/store/src/trie/state_snapshot.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/trie_storage.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/update.rs",
    "nearcore/src/config_validate.rs",
    "nearcore/src/state_sync.rs",
    "neard/src/cli.rs",
    "neard/src/main.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/features.rs",
    "runtime/near-vm-runner/src/imports.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v2.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/mod.rs",
    "runtime/runtime/src/access_keys.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/cache_warming.rs",
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/contract_code.rs",
    "runtime/runtime/src/conversions.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/pipelining.rs",
    "runtime/runtime/src/prefetch.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/types.rs",
    "runtime/runtime/src/verifier.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered Chunk availability, part ownership, erasure coding, chunk reconstruction, or chunk inclusion tracking bug marks missing or unrecoverable chunk data as available for canonical processing.",
    "Critical. Unprivileged-user-triggered Partial encoded chunk, receipt proof, chunk extra, or chunk body validation bug reconstructs a different chunk payload than the one committed by the block/chunk header.",
    "Critical. Unprivileged-user-triggered State witness, partial witness, contract access list, chunk endorsement, or witness acknowledgement logic lets validators endorse a chunk whose execution data was not actually validated for the committed state.",
    "Critical. Unprivileged-user-triggered State sync header, state part, snapshot, external storage, or part ordinal validation bug installs state for the wrong shard, epoch, root, or split boundary.",
    "Critical. Unprivileged-user-triggered Flat storage, memtrie loading, catchup, resharding delta, or trie update replay bug skips, duplicates, or reorders state changes while reconstructing tracked shard state.",
    "High. Unprivileged-user-triggered Missing-chunk, orphan block, optimistic block, cached chunk result, or pending shard job logic attaches valid data to the wrong block, height, shard, or previous state.",
    "High. Unprivileged-user-triggered Shards manager, chunk distribution, state witness distribution, routing target, or validator assignment lookup sends required validation data to the wrong validator set or shard owners with protocol-visible consequences.",
    "High. Unprivileged-user-triggered Garbage collection, split storage, archival boundary, state snapshot retention, or cold storage transition removes data still required for canonical validation, catchup, or state reconstruction.",
    "High. Unprivileged-user-triggered Reed-Solomon, compression, encoded-part serialization, or witness part assembly accepts inconsistent pieces that pass local checks but reconstruct a different committed payload.",
]


def question_generator(target_file: str) -> str:
    """
    Generate data-availability and reconstruction audit questions for one nearcore target.

    target_file format:
    "'File Name: chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs -> Scope: Critical. Unprivileged-user-triggered State witness, partial witness, contract access list, chunk endorsement, or witness acknowledgement logic lets validators endorse a chunk whose execution data was not actually validated for the committed state.'"
    """

    prompt = f"""
    ```

    Generate data-availability and reconstruction security questions for this exact nearcore target:

    {target_file}

    Lens:
    This `nearcore` pass is about whether the node reconstructs, routes, retains, and validates the exact data committed by protocol objects. Do not duplicate consensus-finality or ledger-accounting prompts. Focus on chunks, state parts, witnesses, encoded parts, catchup, flat storage, snapshots, and resharding data movement.

    Relevant mechanisms:
    `PartialEncodedChunk`, `ShardChunk`, `ChunkExtra`, `StateSyncInfo`, `ShardStateSyncResponseHeader`, `StatePartKey`, `validate_state_part`, `PartialEncodedStateWitness`, `ChunkEndorsement`, `ChunkEndorsementsBitmap`, `partial_witness_tracker`, `partial_witness_actor`, `state_witness_producer`, `chunk_validation_actor`, `ShardsManager`, `missing_chunks`, `orphan`, `flat_storage_resharder`, `trie_state_resharder`, `apply_deltas_to_memtries`, `StateSnapshot`, `SplitStorage`, Reed-Solomon encoding, and compression boundaries.

    Ask from these angles:
    * Commitment binding: does the part/body/witness/state data match the hash, root, shard, height, epoch, and owner set it claims?
    * Reconstruction: can valid-looking pieces assemble into the wrong chunk, witness, state part, or trie state?
    * Availability decisions: can nearcore mark data available, validated, endorsed, caught up, or retained when the required data is missing or mismatched?
    * Movement across boundaries: can resharding, catchup, GC, external storage, cold storage, or optimistic caches attach data to the wrong shard or block?
    * Validator targeting: are parts and acknowledgements tied to the correct validator assignment and shard tracking view?

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact.
    * Assume full repo context is accessible; do not ask for code.
    * Attacker must be an unprivileged user: unauthenticated/low-trust peer, public RPC client, ordinary account holder, or contract deployer/caller using public inputs.
    * Unprivileged attacker may control peer-supplied data they are allowed to send, RPC-triggered fetch/query paths, timing/order of public messages, and contract code/input that influences witness contents.
    * Do not grant validator, chunk validator, block producer, chunk producer, state-sync provider operator, node admin, or trusted storage-service privileges unless the bug lets an unprivileged user bypass that boundary.
    * A malicious peer sending bad data is not enough; ask only where nearcore accepts, endorses, installs, caches, routes, or prunes data incorrectly.
    * Reject admin/operator mistakes, manual DB/config edits, debug/adversarial modes, validator/chunk-producer authority, compromised supermajorities, dependency-only bugs, and downstream misuse.
    * Exclude ordinary DoS, unbounded memory/disk/cache growth, OOM, logs, tests, mocks, benches, tooling, and Rust memory-management hygiene unless an in-scope data commitment is corrupted.
    * Generate 16 to 24 high-signal questions, with at least two thirds crossing modules or persisted state transitions.
    * Every question must be testable with `cargo test --package ... --features test_features`, a property/fuzz test, a test-loop test, or a focused local reproducer.
    * Name the exact value that can be wrong: chunk hash, encoded part ordinal, receipts root, chunk extra, state root, state part id, shard id, epoch id, witness hash, endorsement bitmap, owner set, snapshot boundary, trie key/value, or cache entry.

    Each question must include target symbol, attacker-controlled data, preconditions, call path, commitment invariant, exact corrupted availability/reconstruction value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled DATA under PRECONDITIONS pass CALL_PATH and violate COMMITMENT_OR_RECONSTRUCTION_INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a Rust property/test-loop/state-sync reproducer over PARAMETERS and assert EXPECTED_COMMITMENT_BINDING.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a nearcore data-availability question validation prompt.
    """
    return f"""# DATA AVAILABILITY QUESTION REVIEW

## Question
{question}

## Boundary
Audit only production nearcore files listed in `scope_files`. Ignore tests, docs, mocks, benches, fuzz harnesses, generated data, automation, packaging, scripts, and local-only tools. Do not ask for repo contents.

## Goal
Decide whether the question can expose a reachable bug in data reconstruction, availability marking, endorsement, catchup, state sync, snapshotting, GC, resharding data movement, or committed-data binding.

A valid path must show unprivileged-user-controlled data reaching production code and causing nearcore to accept, endorse, install, route, cache, or prune the wrong committed data. Prefer #NoVulnerability unless the exact corrupted chunk/state/witness/part value is concrete.

Do not assume validator, chunk validator, block producer, chunk producer, state-sync provider operator, node admin, or trusted storage-service privileges unless the question proves an unprivileged bypass of that boundary.

## Review Steps
1. Identify the production entrypoint and target symbol.
2. Bind the attacker data to block height, shard id, epoch id, chunk hash, state root, witness hash, owner set, or part ordinal.
3. Trace reconstruction, validation, storage, cache, and routing decisions.
4. Check existing hash/root/proof/owner/epoch/shard/ordinal guards.
5. Name the exact value that becomes wrong and why it survives normal validation.
6. Require file/function references and a local test strategy.

## Fast Rejections
- Only a malicious peer sends data that nearcore rejects, ignores, retries, rate-limits, disconnects, or treats as non-canonical.
- Admin/operator error, manual DB/config/genesis edits, debug/adversarial mode, wrong key custody, or deployment-specific setup.
- Requires validator, chunk validator, block producer, chunk producer, state-sync provider operator, node admin, or trusted storage-service privileges not obtainable by an unprivileged user.
- Ordinary crash, DoS, timeout, resource growth, OOM, leak, logging, style, or Rust memory-management cleanup.
- No exact corrupted chunk/state/witness/part/cache/owner value, or no supported attacker-controlled path.
- Claim belongs only to ledger accounting, authorization, generic finality, or protocol-upgrade compatibility rather than this target scope.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for nearcore data availability issues.
    """
    prompt = f"""# DATA AVAILABILITY ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a seed for a nearcore-native issue in production `scope_files`. Look for analogs in chunk/state/witness reconstruction, encoded part validation, state sync parts, catchup, resharding deltas, snapshots, GC, routing to validator owners, or committed-data caches.

Do not claim missing files. Do not audit tests, docs, mocks, benches, fuzz harnesses, generated data, scripts, packaging, or local tools.

## Analog Standard
Report only if nearcore has its own reachable root cause, unprivileged-user-controlled data, broken commitment/reconstruction invariant, exact corrupted value, and scoped High/Critical impact.

Reject analogs based on admin mistakes, privileged validator/chunk-producer/storage-operator roles, malicious-peer noise that is rejected or only wastes resources, ordinary DoS/resource growth, memory cleanup, dependency-only behavior, or downstream misuse.

## Work Plan
1. Translate the external bug into a nearcore data invariant: hash/root binding, part ordinal, owner set, shard/epoch binding, reconstruction identity, retention boundary, or cache key.
2. Map it to exact production symbols.
3. Trace attacker-controlled data through validation and storage/routing.
4. Identify the exact corrupted chunk hash, state root, witness hash, state part, shard id, epoch id, owner set, snapshot boundary, trie key/value, or cache entry.
5. Reject if existing checks prevent the mismatch or if impact is resource-only.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict nearcore data-availability validation prompt.
    """
    prompt = f"""# DATA AVAILABILITY VALIDATION

## Security Claim
{report}

## Validation Rules
- Validate only this claim against production nearcore files in `scope_files`.
- A valid issue must be reachable by an unprivileged user through public chunk, witness, state-sync, catchup, resharding, snapshot, routing, RPC, contract, or storage request paths.
- The final impact must match one allowed scope below and name the exact corrupted committed-data value.
- Reject admin/operator mistakes, manual DB/config/genesis edits, debug/adversarial modes, compromised supermajorities, dependency-only bugs, downstream misuse, and environment-specific setup.
- Reject claims requiring validator, chunk validator, block producer, chunk producer, state-sync provider operator, node admin, or trusted storage-service privileges unless the report proves an unprivileged user can bypass that boundary.
- Reject malicious-peer-only claims where bad data is rejected, ignored, retried, disconnected, rate-limited, or only wastes resources.
- Reject ordinary crash/DoS, unbounded CPU/memory/disk/cache/queue growth, leaks, OOM, logging/display issues, and Rust memory-management hygiene unless a committed-data value is accepted or installed incorrectly.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unprivileged-user-triggered Chunk availability, part ownership, erasure coding, chunk reconstruction, or chunk inclusion tracking bug marks missing or unrecoverable chunk data as available for canonical processing.
- Critical. Unprivileged-user-triggered Partial encoded chunk, receipt proof, chunk extra, or chunk body validation bug reconstructs a different chunk payload than the one committed by the block/chunk header.
- Critical. Unprivileged-user-triggered State witness, partial witness, contract access list, chunk endorsement, or witness acknowledgement logic lets validators endorse a chunk whose execution data was not actually validated for the committed state.
- Critical. Unprivileged-user-triggered State sync header, state part, snapshot, external storage, or part ordinal validation bug installs state for the wrong shard, epoch, root, or split boundary.
- Critical. Unprivileged-user-triggered Flat storage, memtrie loading, catchup, resharding delta, or trie update replay bug skips, duplicates, or reorders state changes while reconstructing tracked shard state.
- High. Unprivileged-user-triggered Missing-chunk, orphan block, optimistic block, cached chunk result, or pending shard job logic attaches valid data to the wrong block, height, shard, or previous state.
- High. Unprivileged-user-triggered Shards manager, chunk distribution, state witness distribution, routing target, or validator assignment lookup sends required validation data to the wrong validator set or shard owners with protocol-visible consequences.
- High. Unprivileged-user-triggered Garbage collection, split storage, archival boundary, state snapshot retention, or cold storage transition removes data still required for canonical validation, catchup, or state reconstruction.
- High. Unprivileged-user-triggered Reed-Solomon, compression, encoded-part serialization, or witness part assembly accepts inconsistent pieces that pass local checks but reconstruct a different committed payload.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear broken commitment, reconstruction, owner-set, shard/epoch binding, part ordinal, cache-key, retention, or replay invariant.
3. Reachable exploit path: preconditions -> attacker-controlled data -> production call path -> bad committed-data value.
4. Existing hash/root/proof/owner/epoch/shard/ordinal checks reviewed and shown insufficient.
5. Exact corrupted value identified: chunk hash, encoded part, receipts root, chunk extra, state root, state part id, shard id, epoch id, witness hash, endorsement bitmap, owner set, snapshot boundary, trie key/value, or cache entry.
6. Concrete impact matching one allowed scope, with realistic likelihood.
7. Reproducible proof path: Rust unit/property test, state-sync test, test-loop test, or focused local reproducer.
8. No rejection reason from privileged-role requirements, admin error, malicious-peer-only behavior, resource-only behavior, dependency-only behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Which committed-data binding is broken?
- Can an unprivileged user trigger this without validator, chunk validator, block producer, chunk producer, state-sync provider operator, node admin, or trusted storage-service privileges?
- Which supported attacker-controlled chunk/witness/state-sync/catchup input triggers it?
- What exact chunk/state/witness/part/cache value becomes wrong?
- Do existing hash, root, proof, owner, epoch, shard, and ordinal checks already prevent it?
- Is this more than bad peer data being rejected or resource exhaustion?
- What exact test proves the wrong data is accepted, endorsed, installed, cached, routed, or pruned?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
