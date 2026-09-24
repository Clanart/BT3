### Title
Any message unpacking resolves message type via a global default registry instead of the caller's own DescriptorPool/factory - ([File: python/google/protobuf/internal/python_message.py])

### Summary
The pyUmbral report's underlying invariant failure is: an operation that should be scoped to a caller-supplied configuration (the curve tied to a specific key) instead silently falls back to a global default, producing a type/parameter mismatch that the caller cannot detect until a downstream operation fails or behaves incorrectly. The closest structural analog in this Protobuf checkout is `google.protobuf.internal.python_message._InternalUnpackAny`, which resolves the message type for an `Any` payload using the process-wide default `symbol_database.Default()` registry rather than the `DescriptorPool`/`MessageFactory` associated with the message that actually owns the `Any` field.

### Finding Description
`_InternalUnpackAny` is documented as an intentional shortcut: [1](#0-0) 

It always looks up the type using the global default pool/factory: [2](#0-1) 

The `type_name` used for lookup is taken directly from the attacker-controlled `type_url` field of an `Any` message (`type_url.split('/')[-1]`), and is resolved with `factory.pool.FindMessageTypeByName(type_name)` against the *default* `symbol_database`, not the pool that produced/owns the enclosing message: [3](#0-2) 

This is the same class of bug as the pyUmbral report: an operation that carries enough context to be scoped precisely (the message's own pool/factory, analogous to the private key's own curve) instead defers to an ambient global configuration. The TODO comment in the code itself acknowledges the problem: *"To make Any work with custom factories, use the message factory of the parent message."* The recommendation in the report — "define which curve the system is using in a single, canonical location" / "force get_pubkey to use the same curve as the private key it's being generated from" — maps directly onto "resolve the Any's type using the pool of the message that contains it," which is not done here.

### Impact Explanation
When an application uses a non-default `DescriptorPool` (e.g., isolated schema registries, multi-tenant descriptor pools, or dynamically-loaded proto definitions not registered in the global `symbol_database`), unpacking an untrusted `Any` value via this code path can:
- Silently fail to resolve a type that is legitimately registered only in the caller's custom pool, returning `None` (denial of a legitimate operation), or
- Resolve `type_name` against an unrelated message definition that happens to share the same fully-qualified name in the global default pool, producing a type confusion where bytes intended for one schema are parsed as another (the `ParseFromString` call afterward has no cross-check that the resolved descriptor is the one the application intended for that context).

This mirrors the "cannot decrypt any incoming messages" failure mode from the report: the operation "succeeds" in the sense that it returns *a* value, but it is the wrong value/type relative to the caller's actual configuration, and the caller has no signal that a different (global default) resolution path was taken.

### Likelihood Explanation
This code path is reached whenever pure-Python protobuf implementations process an `Any` message and go through Python-level `Any` unpacking helpers that don't have a target message already provided (`_InternalUnpackAny`, used when no explicit target type is passed, as opposed to `well_known_types.Any.Unpack(msg)` which validates against a caller-supplied `msg.DESCRIPTOR`). Any application accepting attacker-controlled binary or ProtoJSON payloads containing `Any` fields, and using a non-default/custom `DescriptorPool` for its schemas, is exposed. This is a legitimate, supported use case in Protobuf's public API surface (custom descriptor pools are a documented feature), and the attacker fully controls the `type_url` string content within the bounds of the (trusted) `Any` schema.

### Recommendation
- Thread the message's own `DescriptorPool`/`MessageFactory` through to `_InternalUnpackAny` instead of unconditionally using `symbol_database.Default()`, exactly as the existing TODO states.
- Prefer the safer, explicit `well_known_types.Any.Unpack(msg)` API (which validates against a caller-provided target descriptor) in application code, and consider deprecating/guarding the pool-inferring variant for untrusted input.
- Canonicalize where "the" pool for a given message tree comes from (e.g., always the pool of `msg.DESCRIPTOR.file.pool`) so resolution can't silently diverge between the global default and a custom pool, similar to the report's recommendation to define curve selection in a single canonical location.

### Proof of Concept
Not independently executed in this session (no code-execution tool available here); the finding is based on static reading of `python/google/protobuf/internal/python_message.py` lines 1094–1136. A reproduction would:
1. Create a custom `DescriptorPool` and register a message type `Foo` only in that pool (not in `symbol_database.Default()`).
2. Serialize an `Any` with `type_url = "type.googleapis.com/Foo"` and `value = Foo(...).SerializeToString()`.
3. Call the internal any-unpacking helper (as invoked by JSON printing/parsing of `Any` without an explicit target) and observe that resolution fails (returns `None`) because it only consults `symbol_database.Default()`, or — if a same-named-but-different `Foo` exists in the global default pool — that the bytes get parsed against the wrong descriptor.

Given index limitations, I could not fully trace every caller of `_InternalUnpackAny` (e.g., JSON `Any` printing paths) in this pass; if you want, I can continue tracing those call sites in a follow-up session to confirm exact reachability from ProtoJSON `MessageToJson`/parsing entry points.

### Citations

**File:** python/google/protobuf/internal/python_message.py (L1094-1136)
```python
def _InternalUnpackAny(msg):
  """Unpacks Any message and returns the unpacked message.

  This internal method is different from public Any Unpack method which takes
  the target message as argument. _InternalUnpackAny method does not have
  target message type and need to find the message type in descriptor pool.

  Args:
    msg: An Any message to be unpacked.

  Returns:
    The unpacked message.
  """
  # TODO: Don't use the factory of generated messages.
  # To make Any work with custom factories, use the message factory of the
  # parent message.
  # pylint: disable=g-import-not-at-top
  from google.protobuf import symbol_database

  factory = symbol_database.Default()

  type_url = msg.type_url

  if not type_url:
    return None

  # TODO: For now we just strip the hostname.  Better logic will be
  # required.
  type_name = type_url.split('/')[-1]
  descriptor = factory.pool.FindMessageTypeByName(type_name)

  if descriptor is None:
    return None

  # Unable to import message_factory at top because of circular import.
  # pylint: disable=g-import-not-at-top
  from google.protobuf import message_factory

  message_class = message_factory.GetMessageClass(descriptor)
  message = message_class()

  message.ParseFromString(msg.value)
  return message
```
