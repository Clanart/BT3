## Analysis: MessageSet duplicate-`type_id` parsing differential across Protobuf language implementations

The CVE's failed invariant is that a value used to determine framing/identity of a downstream payload (chunk length, derived from ignoring "chunk extensions") is interpreted **inconsistently between parsers** that must agree, letting a request boundary be smuggled past a security-relevant checkpoint. The closest structural analog in Protobuf is **MessageSet extension-identity resolution**: `type_id` and `message` fields of a `MessageSet` item can legally arrive in either order and can legally repeat, and different official Protobuf runtimes resolve **duplicate `type_id` tags** with different "first wins" vs. "last wins" policies — meaning the *same* wire bytes can be deserialized as *different extension types* depending on which language/binding parses them.

### Title
Inconsistent duplicate-`type_id` resolution in `MessageSet` parsing across C++/Python/upb ("first wins") vs. Java ("last wins") — ([File: java/core/src/main/java/com/google/protobuf/MessageSetSchema.java])

### Summary
Protobuf's legacy `MessageSet` wire format (`message_set_wire_format = true`) encodes each item as a group containing two fields, `type_id` (2, varint) and `message` (3, length-delimited), which the spec explicitly allows to appear in any order and, in practice, to repeat. Every runtime implements a small state machine to resolve this ambiguity. C++ (`ParseMessageSetItemImpl` in `wire_format_lite.h`, `WireFormat::MessageSetParser` in `wire_format.cc`), the Python pure decoder (`MessageSetItemDecoder` in `decoder.py`), and upb (`upb_Decoder_DecodeMessageSetItem` in `upb/wire/decode.c`) all explicitly **ignore a duplicate `type_id`** once one has already been recorded before a payload is seen (first-tag-wins). Java's three parsing paths — `GeneratedMessageLite.mergeMessageSetExtensionFromCodedStream`, `MessageReflection.mergeMessageSetExtensionFromCodedStream`, and `MessageSetSchema` (used by full, non-lite generated messages) — unconditionally overwrite `typeId` on every occurrence of the type-id tag, i.e. **last-tag-wins**, with no duplicate check at all.

### Finding Description
- C++: `internal::ParseMessageSetItemImpl` only updates `last_type_id` while in state `kNoTag` (first tag seen) or `kHasPayload` (payload already consumed, so this is a legitimately new item); a duplicate `type_id` arriving while already in state `kHasType` (type seen, no payload yet) is silently dropped and the original `type_id` is kept. [1](#0-0) 
- upb explicitly documents and implements the same first-wins/ignore-duplicate rule: [2](#0-1) 
- Python's pure decoder likewise only assigns `type_id` the first time (`if type_id == -1: type_id = temp_type_id`): [3](#0-2) 
- Java's `MessageSetSchema` (used for the standard, non-lite generated Java message parser) unconditionally reassigns `typeId` and re-resolves `extension` on every occurrence of the type-id tag, with no "already seen" guard: [4](#0-3) 
- The same unconditional-overwrite pattern also exists in Java's reflective and lite paths: [5](#0-4) [6](#0-5) 

The C++ conformance/unit test suite even has a dedicated test acknowledging that tag order and duplication in MessageSet items is a real, exercised edge case (`ParseMessageSetWithAnyTagOrder`, `ParseMessageSetWithDuplicateTags`), confirming this is intentional supported behavior rather than an untested corner: [7](#0-6) 

Because a single, bounded, well-formed binary blob containing a `MessageSet` item with a repeated `type_id` tag (e.g. `type_id=A`, `type_id=B`, `message=<payload>`) is accepted by all runtimes but is **not required to be interpreted the same way**, two cooperating Protobuf-consuming services that use different language bindings (a common architecture: a C++/Python edge/validation service in front of a Java backend, or vice versa) can disagree about which extension type the same bytes represent.

### Impact Explanation
This is a parser-differential vulnerability class, structurally identical to the HTTP request-smuggling root cause in the reference CVE: a piece of metadata that determines how to interpret a shared payload (chunk length vs. extension `type_id`) is resolved inconsistently between two components in the same trust boundary. Concretely: if a validating/authorizing component built on C++, Python, or upb inspects a `MessageSet`-encoded message and determines (using first-tag-wins) that the payload belongs to extension `A` (e.g. a benign/low-privilege extension) and allows it through, but the same raw bytes are subsequently parsed by a Java-based backend (last-tag-wins) which instead activates extension `B` (e.g. an administrative or sensitive extension) with the attacker-supplied payload bytes, this results in an authorization/type-confusion bypass — a data-integrity failure where the two components silently disagree about the semantic content of the same bounded, trusted-schema, well-formed message. This matches the required scope: no crash, no memory-growth, no privileged access — purely a decode-ambiguity integrity issue.

### Likelihood Explanation
Requires only: (1) a schema using `option message_set_wire_format = true` (a long-standing, still-supported, still-tested Protobuf feature), (2) a heterogeneous deployment mixing Java with any of {C++, Python, upb} bindings parsing the same wire bytes, and (3) an attacker able to submit a single crafted, well-formed, bounded binary payload through the normal public `ParseFrom`/`MergeFrom` API — no malicious schema, no unbounded input, no privileged access. This is realistic in polyglot microservice architectures where a gateway/validator and a backend are written in different languages but share proto schemas.

### Recommendation
Standardize duplicate `type_id` (and duplicate `message`) resolution semantics for `MessageSet` items across all officially supported runtimes — either uniformly adopt "first tag wins" (matching C++/Python/upb, and matching the documented behavior validated by `wire_format_unittest.h`) in Java's `MessageSetSchema`, `MessageReflection`, and `GeneratedMessageLite`, or uniformly adopt "last tag wins" everywhere, and add a cross-language conformance test (in `conformance/binary_json_conformance_suite.cc`) that specifically constructs a `MessageSet` item with duplicate `type_id` tags before the payload and asserts identical resolved-extension results across all languages.

### Proof of Concept
Construct (conceptually) a `MessageSet` item byte sequence:
```
START_GROUP(1)
  type_id = A   (tag 2, varint)
  type_id = B   (tag 2, varint)   // duplicate, arrives before message
  message = <payload bytes>
END_GROUP(1)
```
- Parsing with C++ (`wire_format_lite.h` `ParseMessageSetItemImpl`, lines 1639-1661) or upb (`decode.c`, lines 678-692) or Python (`decoder.py`, lines 900-907): the item resolves to extension **A** — the first `type_id` seen before any payload wins.
- Parsing the identical bytes with Java's `MessageSetSchema.mergeMessageSetItemFromCodedStream` (lines 336-341) or `MessageReflection`/`GeneratedMessageLite`: `typeId` is unconditionally reassigned to **B** on the second occurrence, so the item resolves to extension **B**.

This divergence is confirmed directly by reading the four implementations' state-machine logic; no test execution was performed, and this description does not claim a test run — it is a static code-level demonstration that the two code paths implement different duplicate-resolution policies on the same input bytes.

### Citations

**File:** src/google/protobuf/wire_format_lite.h (L1639-1661)
```text
      case WireFormatLite::kMessageSetTypeIdTag: {
        uint32_t type_id;
        // We should fail parsing if type id is 0.
        if (!input->ReadVarint32(&type_id) || type_id == 0) return false;
        if (state == State::kNoTag) {
          last_type_id = type_id;
          state = State::kHasType;
        } else if (state == State::kHasPayload) {
          // We saw some message data before the type_id.  Have to parse it
          // now.
          io::CodedInputStream sub_input(
              reinterpret_cast<const uint8_t*>(message_data.data()),
              static_cast<int>(message_data.size()));
          sub_input.SetRecursionLimit(input->RecursionBudget());
          if (!ms.ParseField(type_id, &sub_input)) {
            return false;
          }
          message_data.clear();
          state = State::kDone;
        }

        break;
      }
```

**File:** upb/wire/decode.c (L678-692)
```c
    switch (tag) {
      case kEndItemTag:
        return ptr;
      case kTypeIdTag: {
        uint64_t tmp;
        ptr = upb_WireReader_ReadVarint(ptr, &tmp, EPS(d));
        if (state_mask & kUpb_HaveId) break;  // Ignore dup.
        state_mask |= kUpb_HaveId;
        type_id = tmp;
        if (state_mask & kUpb_HavePayload) {
          upb_Decoder_AddMessageSetItem(d, msg, layout, type_id, preserved.data,
                                        preserved.size);
        }
        break;
      }
```

**File:** python/google/protobuf/internal/decoder.py (L900-907)
```python
    # Technically, type_id and message can appear in any order, so we need
    # a little loop here.
    while 1:
      tag_bytes, pos = local_ReadTag(buffer, pos)
      if tag_bytes == type_id_tag_bytes:
        temp_type_id, pos = local_DecodeVarint(buffer, pos)
        if type_id == -1:
          type_id = temp_type_id
```

**File:** java/core/src/main/java/com/google/protobuf/MessageSetSchema.java (L336-341)
```java
      final int tag = reader.getTag();
      if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
        typeId = reader.readUInt32();
        extension =
            extensionSchema.findExtensionByNumber(extensionRegistry, defaultInstance, typeId);
        continue;
```

**File:** java/core/src/main/java/com/google/protobuf/MessageReflection.java (L1359-1371)
```java
      if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
        typeId = input.readUInt32();
        if (typeId != 0) {
          // extensionRegistry may be either ExtensionRegistry or
          // ExtensionRegistryLite. Since the type we are parsing is a full
          // message, only a full ExtensionRegistry could possibly contain
          // extensions of it. Otherwise we will treat the registry as if it
          // were empty.
          if (extensionRegistry instanceof ExtensionRegistry) {
            extension =
                target.findExtensionByNumber((ExtensionRegistry) extensionRegistry, type, typeId);
          }
        }
```

**File:** java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java (L852-856)
```java
        if (tag == WireFormat.MESSAGE_SET_TYPE_ID_TAG) {
          typeId = input.readUInt32();
          if (typeId != 0) {
            extension = extensionRegistry.findLiteExtensionByNumber(defaultInstance, typeId);
          }
```

**File:** src/google/protobuf/wire_format_unittest.h (L731-769)
```text
TYPED_TEST_P(WireFormatTest, ParseMessageSetWithAnyTagOrder) {
  std::string start = BuildMessageSetItemStart();
  std::string end = BuildMessageSetItemEnd();
  std::string id = BuildMessageSetItemTypeId(
      TestFixture::TestMessageSetExtension1::descriptor()
          ->extension(0)
          ->number());
  std::string message = this->BuildMessageSetTestExtension1();

  this->ValidateTestMessageSet("id + message", start + id + message + end);
  this->ValidateTestMessageSet("message + id", start + message + id + end);
}

TYPED_TEST_P(WireFormatTest, ParseMessageSetWithDuplicateTags) {
  std::string start = BuildMessageSetItemStart();
  std::string end = BuildMessageSetItemEnd();
  std::string id = BuildMessageSetItemTypeId(
      TestFixture::TestMessageSetExtension1::descriptor()
          ->extension(0)
          ->number());
  std::string other_id = BuildMessageSetItemTypeId(123456);
  std::string message = this->BuildMessageSetTestExtension1();
  std::string other_message = this->BuildMessageSetTestExtension1(321);

  // Double id
  this->ValidateTestMessageSet("id + other_id + message",
                               start + id + other_id + message + end);
  this->ValidateTestMessageSet("id + message + other_id",
                               start + id + message + other_id + end);
  this->ValidateTestMessageSet("message + id + other_id",
                               start + message + id + other_id + end);
  // Double message
  this->ValidateTestMessageSet("id + message + other_message",
                               start + id + message + other_message + end);
  this->ValidateTestMessageSet("message + id + other_message",
                               start + message + id + other_message + end);
  this->ValidateTestMessageSet("message + other_message + id",
                               start + message + other_message + id + end);
}
```
